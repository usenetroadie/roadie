import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from roadie import drafts, i18n
from roadie.secretstore import SecretStore
from roadie.ui import Prompter
from tests.test_questions import USENET_LINES, USENET_SECRETS, FakeChecks


class DraftStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SecretStore(Path(self.tmp.name) / "secrets.json")

    def test_draft_roundtrip(self):
        drafts.save(self.store, {"admin_user": "streamadmin", "auth": {"password": "pw"}}, ["server"])
        data, done = drafts.load(SecretStore(self.store.path))
        self.assertEqual(data["auth"]["password"], "pw")
        self.assertEqual(done, ["server"])

    def test_draft_other_version_ignored(self):
        self.store.set(drafts.KEY, json.dumps({"version": "0.0.1", "data": {}, "done": ["server"]}))
        self.assertIsNone(drafts.load(self.store))

    def test_draft_broken_ignored(self):
        self.store.set(drafts.KEY, "{not json")
        self.assertIsNone(drafts.load(self.store))

    def test_draft_cleared(self):
        drafts.save(self.store, {"a": 1}, ["server"])
        drafts.clear(self.store)
        self.assertIsNone(drafts.load(self.store))

    def test_draft_is_not_a_redaction_value(self):
        drafts.save(self.store, {"a": 1}, ["server"])
        self.assertFalse(any(v.startswith("{") for v in self.store.values()))


class ResumeTest(unittest.TestCase):
    """Ctrl+C in the middle of the questions: the next start offers to go on."""

    def run_questions(self, lines, secrets, start=None, done=None, saved=None):
        from roadie.questions import ask_all
        out = io.StringIO()
        queue = list(secrets)
        ui = Prompter(inp=io.StringIO("\n".join(lines) + "\n"), out=out, secret_reader=lambda p: queue.pop(0))
        saved = saved if saved is not None else []
        try:
            data = ask_all(ui, FakeChecks(), start=start, done=done,
                           on_part=lambda d, dn: saved.append((copy.deepcopy(d), list(dn))))
        except EOFError:  # like Ctrl+C: the input ends in the middle
            data = None
        return data, out.getvalue(), saved

    def test_interrupt_keeps_answers(self):
        cut = USENET_LINES[:USENET_LINES.index("My indexer")]  # ends in part 3 (Usenet)
        _, _, saved = self.run_questions(cut, USENET_SECRETS)
        data, done = saved[-1]
        self.assertEqual(done, ["server", "domain"])
        rest = USENET_LINES[USENET_LINES.index("Example.duckdns.org") + 1:]  # from the Usenet part on
        result, out, _ = self.run_questions(rest, USENET_SECRETS[1:], start=data, done=done)
        self.assertEqual(result["admin_user"], "streamadmin")
        self.assertEqual(result["ssh_public_key"], data["ssh_public_key"])  # the same key, not a new one
        self.assertNotIn(i18n.t("start.title"), out)  # start page only on a new start

    def test_parts_done_are_not_asked_again(self):
        _, _, saved = self.run_questions(USENET_LINES, USENET_SECRETS)
        self.assertEqual(saved[-1][1][-1], "operations")


if __name__ == "__main__":
    unittest.main()
