import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import cli
from roadie.paths import Paths
from roadie.ui import Prompter


class RestoreMenuTest(unittest.TestCase):
    """Menu "Restore a backup": pick, key, warning, the word "restore", then the setup steps."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(Path(self.tmp.name))
        self.archive = Path(self.tmp.name) / "roadie-backup-2026-11-20.tar.age"
        self.archive.write_text("x")
        self.env = mock.Mock()

    def tearDown(self):
        self.tmp.cleanup()

    def run_menu(self, lines, key_ok=True, restored=0):
        from roadie.ops import backup
        ui = Prompter(inp=io.StringIO("\n".join(lines) + "\n"), out=io.StringIO(), secret_reader=lambda p: "AGE-KEY")
        with mock.patch.object(backup, "archives", return_value=[self.archive]), \
                mock.patch.object(backup, "key_opens", return_value=key_ok), \
                mock.patch.object(backup, "restore_running", return_value=restored) as restore, \
                mock.patch.object(cli, "_run_setup", return_value=0) as setup, \
                mock.patch.object(cli.Answers, "restore"):
            code = cli._restore_menu(self.env, self.paths, ui, None, None)
        return code, restore, setup, ui.out.getvalue()

    def test_cancelled_without_the_word(self):
        code, restore, setup, out = self.run_menu(["1", ""])
        self.assertEqual(code, 0)
        restore.assert_not_called()
        setup.assert_not_called()

    def test_wrong_key_changes_nothing(self):
        code, restore, _, _ = self.run_menu(["1"], key_ok=False)
        self.assertEqual(code, 1)
        restore.assert_not_called()

    def test_restore_then_setup_runs_like_an_update(self):
        code, restore, setup, out = self.run_menu(["1", "restore"])
        self.assertEqual(code, 0)
        restore.assert_called_once_with(self.env, self.archive, "AGE-KEY")
        self.assertTrue(setup.call_args.kwargs.get("update"))

    def test_failed_restore_does_not_run_setup(self):
        code, _, setup, _ = self.run_menu(["1", "restore"], restored=1)
        self.assertEqual(code, 1)
        setup.assert_not_called()

    def test_back(self):
        code, restore, _, _ = self.run_menu(["2"])
        self.assertEqual(code, 0)
        restore.assert_not_called()
