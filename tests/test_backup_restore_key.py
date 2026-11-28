import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie.ops import backup
from roadie.paths import Paths


class RestoreWithKeyTest(unittest.TestCase):
    def test_restore_with_key_removes_temp_file(self):
        seen = []

        def fake_restore(paths, archive, identity, overwrite=False, then_continue=False, same_server=False):
            seen.append(identity)
            self.assertEqual(Path(identity).read_text(), "AGE-SECRET-KEY-1TEST\n")
            self.assertEqual(Path(identity).stat().st_mode & 0o777, 0o600)
            raise RuntimeError("wrong key")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backup, "restore", side_effect=fake_restore):
            with self.assertRaises(RuntimeError):
                backup.restore_with_key(Paths(Path(tmp)), "x.tar.age", "  AGE-SECRET-KEY-1TEST  ")
        self.assertFalse(Path(seen[0]).exists())

    def test_restore_with_key_passes_on_continue(self):
        seen = {}

        def fake_restore(paths, archive, identity, overwrite=False, then_continue=False, same_server=False):
            seen["continue"] = then_continue
            return 0
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backup, "restore", side_effect=fake_restore):
            backup.restore_with_key(Paths(Path(tmp)), "x.tar.age", "AGE-SECRET-KEY-1TEST", then_continue=True)
        self.assertTrue(seen["continue"])

    def test_find_uploaded_picks_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "root").mkdir()
            (root / "home/streamadmin").mkdir(parents=True)
            old = root / "root/roadie-backup-2026-11-01.tar.age"
            new = root / "home/streamadmin/roadie-backup-2026-11-19.tar.age"
            old.write_bytes(b"a")
            new.write_bytes(b"b")
            os.utime(old, (1, 1))
            self.assertEqual(backup.find_uploaded(Paths(root)), new)
            self.assertIsNone(backup.find_uploaded(Paths(root / "nothing")))


if __name__ == "__main__":
    unittest.main()
