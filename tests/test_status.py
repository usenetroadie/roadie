import json
import tempfile
import unittest
from pathlib import Path

from roadie import i18n
from roadie.ops import status
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.phases import ops as ops_phase
from roadie.secretstore import SecretStore
from roadie.state import State

PROBLEM_KEYS = ["watch.container_down", "watch.container_unhealthy", "watch.unreachable", "watch.tls_expiring",
                "watch.indexer_disabled", "watch.disk_full", "watch.ram_low"]


class StatusTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths(Path(self.tmp.name))
        self.paths.etc_dir.mkdir(parents=True)
        self.paths.state_file.write_text(json.dumps({"answers": {"domain": "example.duckdns.org"}, "done": []}))
        store = SecretStore(self.paths.secrets_file)
        store.set("aiostreams_config_uuid", "UUID-SECRET")
        store.set("aiostreams_config_encrypted", "ENC-SECRET")
        self.env = Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))
        self.env.services = lambda: []
        self.problems = {}
        self.env.load_json = lambda name: ({"problems": self.problems} if name == "watch.json" else {})

    def test_status_motd_hint(self):
        self.assertNotIn(i18n.t("status.menu_hint"), "\n".join(status.lines(self.env)))
        self.assertIn(i18n.t("status.menu_hint"), "\n".join(status.lines(self.env, motd=True)))
        self.assertIn("sudo roadie", i18n.t("status.menu_hint"))

    def test_login_message_shows_an_update_in_red_first(self):
        from unittest import mock
        from roadie.ops import selfupdate
        self.env.load_json = lambda name: ({"selfupdate": {"notified": "v9.0.0", "news": ["Faster setup"]}}
                                           if name == "watch.json" else {})
        with mock.patch.object(selfupdate, "__version__", "1.0.0"):
            out = status.lines(self.env, motd=True, cols=200)
        self.assertIn("\033[1;31m", out[1])  # right under the title, bold red
        self.assertIn("1.0.0 → 9.0.0", out[1])
        self.assertIn("sudo roadie update", out[2])
        self.assertIn("  - Faster setup", out)
        with mock.patch.object(selfupdate, "__version__", "9.0.0"):  # already updated: nothing
            self.assertNotIn("9.0.0", "\n".join(status.lines(self.env, motd=True, cols=200)))

    def test_status_problem_has_fix(self):
        self.problems = {"c": {"key": "watch.container_down", "params": {"name": "aiostreams"}, "notified_at": 1}}
        text = "\n".join(status.lines(self.env, cols=200))
        self.assertIn("→ " + i18n.t("watch.container_down.fix"), text)

    def test_what_to_do_wrapped_under_arrow(self):
        self.problems = {"c": {"key": "watch.unreachable", "params": {"host": "aiostreams.example.duckdns.org"},
                               "notified_at": 1}}
        lines = status.lines(self.env, cols=50)
        self.assertTrue(all(len(line) <= 49 for line in lines), lines)
        fix = [i for i, line in enumerate(lines) if line.startswith("    → ")]
        self.assertEqual(len(fix), 1)
        rest = lines[fix[0] + 1:]
        self.assertTrue(rest[0].startswith("      ") and not rest[0].startswith("       "), rest)
        joined = " ".join(line.strip() for line in lines[fix[0]:] if line.startswith("    "))
        self.assertIn(" ".join(i18n.t("watch.unreachable.fix").split()), joined)

    def test_default_width_without_terminal_is_50(self):
        from unittest import mock
        with mock.patch.object(status.sys.stdout, "isatty", return_value=False):
            self.assertEqual(status.width(), 50)

    def test_terminal_width_beats_stale_columns(self):
        import os
        from unittest import mock
        with mock.patch.object(status.sys.stdout, "isatty", return_value=True), \
                mock.patch.object(status.sys.stdout, "fileno", return_value=1, create=True), \
                mock.patch.dict(os.environ, {"COLUMNS": "100", "LINES": "30"}), \
                mock.patch("roadie.ui.os.get_terminal_size", return_value=mock.Mock(columns=53)):
            self.assertEqual(status.width(), 53)

    def test_every_problem_key_has_fix_in_both_languages(self):
        for lang in ("en", "de"):
            i18n.set_language(lang)
            for key in PROBLEM_KEYS:
                self.assertNotEqual(i18n.t(key + ".fix"), key + ".fix", (lang, key))

    def test_tls_fix_says_certificates_renew_themselves(self):
        for lang in ("en", "de"):
            i18n.set_language(lang)
            fix = i18n.t("watch.tls_expiring.fix")
            self.assertIn("443", fix)
            self.assertIn("DuckDNS", fix)
            self.assertNotIn("80 ", fix)
        i18n.set_language("en")

    def test_motd_status_has_no_secrets(self):
        self.problems = {"c": {"key": "watch.container_down", "params": {"name": "UUID-SECRET/ENC-SECRET"},
                               "notified_at": 1}}
        self.env.load_json = lambda name: ({"problems": self.problems} if name == "watch.json"
                                           else {"time": "2026-11-14T04:07:00", "file": "/home/x/b.tar.age"})
        text = "\n".join(status.lines(self.env, motd=True, cols=200))
        self.assertIn("***", text)  # the problem line is there, its secret part is hidden
        self.assertIn("2026-11-14 04:07:00", text)
        for secret in ("UUID-SECRET", "ENC-SECRET"):
            self.assertNotIn(secret, text)


class QuietMotdTest(unittest.TestCase):
    def test_quiet_ubuntu_motd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "etc/update-motd.d"
            d.mkdir(parents=True)
            for name in ops_phase.QUIET_MOTD:
                (d / name).write_text("#!/bin/sh\n")
                (d / name).chmod(0o755)
            (d / "00-header").write_text("#!/bin/sh\n")
            (d / "00-header").chmod(0o755)
            (root / "etc/default").mkdir(parents=True)
            (root / "etc/default/motd-news").write_text("ENABLED=1\nURL=x\n")
            ops_phase.quiet_ubuntu_motd(root)
            for name in ops_phase.QUIET_MOTD:
                self.assertFalse((d / name).stat().st_mode & 0o111, name)
            self.assertTrue((d / "00-header").stat().st_mode & 0o111)
            self.assertIn("ENABLED=0", (root / "etc/default/motd-news").read_text())
            ops_phase.quiet_ubuntu_motd(Path(tmp) / "missing")  # nothing there: no error
