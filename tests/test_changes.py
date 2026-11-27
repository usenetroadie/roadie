import io
import json
import unittest
import unittest.mock
import tempfile
from pathlib import Path

from roadie.changes import changed_parts

BASE = {"language": "en", "content": {"a": 1}, "indexers": [{"name": "x"}], "people": [], "_ssh_ports": [22]}


class ChangedPartsTest(unittest.TestCase):
    def test_fresh_setup_is_everything(self):
        self.assertEqual(changed_parts(None, BASE), {"*"})

    def test_same_answers_nothing_changed(self):
        self.assertEqual(changed_parts(BASE, dict(BASE)), set())

    def test_only_content_changed(self):
        self.assertEqual(changed_parts(BASE, {**BASE, "content": {"a": 2}}), {"content"})

    def test_markers_ignored(self):
        self.assertEqual(changed_parts(BASE, {**BASE, "_ssh_ports": [2222], "_new": 1}), set())

    def test_new_person_marks_people(self):
        self.assertEqual(changed_parts(BASE, {**BASE, "people": [{"name": "bo"}]}), {"people"})

    def test_added_and_removed_keys(self):
        cur = {k: v for k, v in BASE.items() if k != "content"}
        self.assertEqual(changed_parts(BASE, {**cur, "notify": {}}), {"content", "notify"})


class RunSetupWiringTest(unittest.TestCase):
    def run_with(self, state_answers, root=None, fail=False, change=None):
        from roadie import cli
        from roadie.engine import Step
        from roadie.ui import Prompter
        from tests.test_cli import fake_facts, EXAMPLE
        if root is None:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name)
            (root / "etc/roadie").mkdir(parents=True)
        self.root = root
        paths = cli.Paths(root)
        if state_answers == "keep":
            pass
        elif state_answers is None:  # a previous run stored the example answers (secrets in the store)
            from roadie.answers import Answers as A
            from roadie.secretstore import SecretStore
            from roadie.state import State
            st = State(paths.state_file)
            A.from_dict(json.loads(EXAMPLE.read_text())).store(st, SecretStore(paths.secrets_file))
            st.save()
        else:
            (root / "etc/roadie/state.json").write_text(json.dumps({"answers": state_answers, "done": []}))
        seen = {}
        def body(ctx):
            seen.update(ctx.extra)
            if fail:
                from roadie.engine import StepFailed
                raise StepFailed("summary", "boom")
        steps = lambda a: [Step("summary", "step.summary", body, always=True)]
        from roadie.answers import Answers
        data = json.loads(EXAMPLE.read_text())
        if change:
            data.update(change)
        answers = Answers.from_dict(data)
        ui = Prompter(inp=io.StringIO(""), out=io.StringIO())
        from roadie.redact import Redactor
        cli._run_setup(answers, cli.Paths(root), ui, fake_facts, Redactor(()), False, steps)
        return seen

    def test_rerun_with_same_answers_changes_nothing(self):
        self.assertEqual(self.run_with(None)["changed"], set())

    def test_fresh_state_is_everything(self):
        self.assertEqual(self.run_with({"_x": 1})["changed"], {"*"})

    def test_failed_run_keeps_changes_for_next_run(self):
        self.run_with(None)
        changed = {"content": {**json.loads(__import__("tests.test_cli", fromlist=["EXAMPLE"]).EXAMPLE.read_text())["content"], "hdr": False}}
        self.assertIn("content", self.run_with("keep", self.root, fail=True, change=changed)["changed"])
        self.assertIn("content", self.run_with("keep", self.root, change=changed)["changed"])
        self.assertEqual(self.run_with("keep", self.root, change=changed)["changed"], set())

    def test_failed_fresh_run_stays_everything(self):
        self.assertEqual(self.run_with({"_x": 1}, fail=True)["changed"], {"*"})
        self.assertEqual(self.run_with("keep", self.root)["changed"], {"*"})
