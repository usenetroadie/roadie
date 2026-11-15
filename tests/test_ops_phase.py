import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import i18n
from roadie.engine import Context, Outcome
from roadie.paths import Paths
from roadie.phases import ops
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State


class FakeUi:
    def __init__(self, choices=(), confirms=()):
        self.choices = list(choices)
        self.confirms = list(confirms)
        self.events = []
        self.options = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.events.append((name, args))
        return record

    def choose(self, label, options, default=None, hints=None):
        self.events.append(("choose", label))
        self.options.append([key for key, _ in options])
        return self.choices.pop(0)

    def confirm(self, label, default=True):
        self.events.append(("confirm", label))
        return self.confirms.pop(0)

    def countdown(self, seconds, key, sleep=None):
        self.events.append(("countdown", seconds))

    def names(self):
        return [e[0] for e in self.events]


def make_ctx(ui, interactive=True):
    tmp = Path(tempfile.mkdtemp())
    paths = Paths(root=tmp)
    (tmp / "etc/roadie").mkdir(parents=True)
    return Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                   ui=ui, redact=Redactor(), answers=mock.Mock(data={}), extra={"interactive": interactive})


class PushTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.sent, self.push_ok = [], True
        env = mock.Mock()
        env.push.side_effect = lambda *a: self.sent.append(a) or self.push_ok
        p = mock.patch.object(ops, "_env", return_value=env)
        p.start()
        self.addCleanup(p.stop)

    def run_push(self, choices, interactive=True):
        ui = FakeUi(choices)
        out = ops.notifications(make_ctx(ui, interactive))
        return ui, out

    def test_push_only_after_subscribed(self):
        ui, out = self.run_push(["yes", "yes"])
        names = ui.names()
        self.assertEqual(len(self.sent), 1)
        self.assertLess(names.index("choose"), names.index("countdown"))
        self.assertEqual(out.status, "ok")
        self.assertIn("copyable", names)

    def test_push_later_sends_nothing(self):
        ui, out = self.run_push(["later"])
        self.assertEqual(self.sent, [])
        self.assertNotIn("countdown", ui.names())
        self.assertEqual(out.status, "ok")
        self.assertIsNotNone(out.note)

    def test_push_again_resends_with_tips(self):
        ui, out = self.run_push(["yes", "again", "yes"])
        self.assertEqual(len(self.sent), 2)
        self.assertIn("hint", ui.names())
        self.assertEqual(ui.names().count("countdown"), 2)
        self.assertEqual(out.status, "ok")

    def test_push_gives_up_after_three_resends(self):
        ui, out = self.run_push(["yes", "again", "again", "again", "later"])
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(out.status, "ok")
        self.assertEqual(out.note, i18n.t("ops.push.note_unconfirmed"))
        self.assertEqual(ui.options[-1], ["yes", "later"])  # no resend promised when none is left
        self.assertTrue(all("again" in o for o in ui.options[1:-1]))

    def test_push_tips_name_the_server(self):
        ui, _ = self.run_push(["yes", "again", "yes"])
        hint = next(e[1][0] for e in ui.events if e[0] == "hint")
        self.assertIn("https://ntfy.sh", hint)

    def test_failed_send_is_a_warning(self):
        self.push_ok = False
        for choices, interactive in ((["yes"], True), ([], False)):
            ui, out = self.run_push(choices, interactive)
            self.assertEqual((out.status, out.note), ("warn", i18n.t("ops.push.note_failed")))
            self.assertIn("warn", ui.names())

    def test_push_unattended_unchanged(self):
        ui, out = self.run_push([], interactive=False)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("choose", ui.names())
        self.assertNotIn("countdown", ui.names())
        self.assertIsNone(out)


class OfferClearTest(unittest.TestCase):
    def test_offer_clear_after_backup_key(self):
        ui = FakeUi(confirms=[False, True])
        ctx = make_ctx(ui)
        out = "# created\n# public key: age1abc\nAGE-SECRET-KEY-1XYZ\n"
        with mock.patch.object(ops.system, "run", return_value=out):
            ops.backup_key(ctx)
        names = ui.names()
        self.assertEqual(names.count("offer_clear"), 1)
        self.assertEqual(names[-1], "offer_clear")
        self.assertIn("alert", names)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "needs ssh-keygen")
    def test_offer_clear_after_ssh_key(self):
        from roadie.questions import _create_ssh_key
        ui = FakeUi(confirms=[True])
        _create_ssh_key(ui, "ubuntu")
        names = ui.names()
        self.assertEqual(names.count("offer_clear"), 1)
        self.assertEqual(names[-1], "offer_clear")
        self.assertIn("alert", names)
        key_lines = [e[1][0] for e in ui.events if e[0] == "copyable"]
        self.assertEqual(key_lines[0], "-----BEGIN OPENSSH PRIVATE KEY-----")
        self.assertEqual(key_lines[-1], "-----END OPENSSH PRIVATE KEY-----")
        self.assertFalse(any("PRIVATE KEY" in str(e[1]) for e in ui.events if e[0] == "info"))


if __name__ == "__main__":
    unittest.main()
