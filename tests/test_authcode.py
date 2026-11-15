import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from roadie import authcode, i18n
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter

DATA = Path(__file__).parent / "data"
# Both fixtures are real Authelia 4.39 output (file notifier, one message per file, overwritten each time),
# with code, domain, user, IP and tokens replaced by placeholders.


class AuthcodeTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def test_latest_code_from_fixture(self):
        code, when = authcode.latest(DATA / "authelia-notification.txt")
        self.assertEqual(code, "123456")
        self.assertEqual(when, datetime(2026, 11, 14, 20, 27, 37, tzinfo=timezone.utc))

    def test_latest_link_from_fixture(self):
        link, when = authcode.latest(DATA / "authelia-notification-link.txt")
        self.assertEqual(link, "https://auth.example.duckdns.org/reset-password/step2?token=TOKEN123")
        self.assertEqual(when.minute, 30)

    def test_newest_of_several_messages(self):
        text = (DATA / "authelia-notification-link.txt").read_text() + "\n" + (DATA / "authelia-notification.txt").read_text()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.txt"
            p.write_text(text)
            self.assertEqual(authcode.latest(p)[0], "123456")

    def test_no_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(authcode.latest(Path(d) / "missing.txt"))
            empty = Path(d) / "empty.txt"
            empty.write_text("")
            self.assertIsNone(authcode.latest(empty))

    def test_show(self):
        with tempfile.TemporaryDirectory() as d:
            paths = Paths(Path(d))
            paths.etc_dir.mkdir(parents=True)
            paths.state_file.write_text(json.dumps({"answers": {"duckdns": {"subdomain": "example"}}}))
            env = Env(paths, State(paths.state_file), SecretStore(paths.secrets_file))
            out = io.StringIO()
            self.assertEqual(authcode.show(env, Prompter(inp=io.StringIO(""), out=out)), 0)
            self.assertIn(i18n.t("authcode.none"), " ".join(out.getvalue().split()))
            target = paths.stack_dir / "data/authelia/notification.txt"
            target.parent.mkdir(parents=True)
            target.write_text((DATA / "authelia-notification.txt").read_text())
            out = io.StringIO()
            authcode.show(env, Prompter(inp=io.StringIO(""), out=out))
            self.assertIn("123456", out.getvalue())
            self.assertIn(datetime(2026, 11, 14, 20, 27, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"), out.getvalue())


if __name__ == "__main__":
    unittest.main()
