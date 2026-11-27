"""stack_files: the user's own Authelia rules and users.yml (kept unless the login changed)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import system
from roadie.answers import Answers
from roadie.engine import Context
from roadie.paths import Paths
from roadie.phases import stack
from roadie.redact import Redactor
from roadie.render import build_render_config, load_versions
from roadie.secretstore import SecretStore
from roadie.state import State

try:  # own-rules.yml needs PyYAML (python3-yaml); minimal test images lack it
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())


class Ui:
    def __init__(self):
        self.warnings = []

    def warn(self, text):
        self.warnings.append(text)


class StackAutheliaFilesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "etc/roadie").mkdir(parents=True)
        paths = Paths(root=self.root)
        answers = Answers.from_dict(EXAMPLE)
        self.ctx = Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                           ui=Ui(), redact=Redactor(), answers=answers)
        self.ctx.extra["render"] = build_render_config(answers, load_versions(), mem_mb=4000, disk_gb=60)
        self.stack = paths.stack_dir
        self.runs = []
        for target, new in (("stack.pull_image", lambda image: None),
                            ("stack.statuspage.write", lambda env: None),
                            ("system.authelia_hash", lambda image, pw: "$argon2id$v=19$m=1,t=1,p=1$c2FsdA$aGFzaA"),
                            ("system.run", lambda cmd, **kw: self.runs.append(cmd) or "")):
            patcher = mock.patch(f"roadie.phases.{target}" if target.startswith("stack") else f"roadie.{target}", new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def files(self):
        return self.stack / "config/authelia"

    @unittest.skipUnless(HAVE_YAML, "needs python3-yaml")

    def test_own_rules_land_in_configuration(self):
        self.files().mkdir(parents=True)
        (self.files() / "own-rules.yml").write_text("- domain: x.example.org\n  policy: bypass\n")
        stack.stack_files(self.ctx)
        self.assertIn("x.example.org", (self.files() / "configuration.yml").read_text())
        self.assertEqual(self.ctx.ui.warnings, [])

    def test_bad_rules_warn_and_setup_goes_on(self):
        self.files().mkdir(parents=True)
        (self.files() / "own-rules.yml").write_text("- policy: bypass\n")
        stack.stack_files(self.ctx)
        self.assertTrue((self.files() / "configuration.yml").is_file())
        self.assertEqual(len(self.ctx.ui.warnings), 1)
        self.assertIn("own-rules.yml", self.ctx.ui.warnings[0])

    def test_python3_yaml_installed_once_when_missing(self):
        self.files().mkdir(parents=True)
        (self.files() / "own-rules.yml").write_text("- domain: x.example.org\n  policy: bypass\n")
        saved = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        self.addCleanup(lambda: sys.modules.__setitem__("yaml", saved) if saved else sys.modules.pop("yaml", None))
        stack.stack_files(self.ctx)
        installs = [c for c in self.runs if c[:2] == ["apt-get", "install"]]
        self.assertEqual(installs, [["apt-get", "install", "-y", "-q", "python3-yaml"]])
        self.assertEqual(len(self.ctx.ui.warnings), 1)  # still missing here: rules ignored, setup goes on

    def test_failing_apt_install_does_not_abort(self):
        self.files().mkdir(parents=True)
        (self.files() / "own-rules.yml").write_text("- domain: x.example.org\n  policy: bypass\n")
        saved = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        self.addCleanup(lambda: sys.modules.__setitem__("yaml", saved) if saved else sys.modules.pop("yaml", None))
        for error in (FileNotFoundError("apt-get"), subprocess.TimeoutExpired("apt-get", 1),
                      system.CommandFailed(["apt-get"], 100, "")):
            def run(cmd, **kw):
                if cmd[0] == "apt-get":
                    raise error
                return ""
            with mock.patch("roadie.system.run", run):
                stack.stack_files(self.ctx)  # must not raise
            self.assertTrue((self.files() / "configuration.yml").is_file())

    def test_unhealthy_authelia_points_at_own_rules(self):
        class Ui2(Ui):
            def fail(self, text):
                self.warnings.append(text)

            def hint(self, text):
                self.warnings.append("HINT " + text)
        self.ctx.ui = Ui2()
        for used, expect in ((True, True), (False, False)):
            self.ctx.ui.warnings.clear()
            self.ctx.extra["own_rules_used"] = used
            with mock.patch.object(stack, "_unhealthy", lambda ctx: ["authelia"]), \
                    mock.patch.object(stack, "_without_own_rules", lambda ctx: False), \
                    mock.patch.object(stack.time, "time", side_effect=[0, 1000, 1000, 1000]):
                self.assertFalse(stack.stack_healthy(self.ctx))
            hints = [w for w in self.ctx.ui.warnings if w.startswith("HINT")]
            self.assertEqual(bool(hints), expect)
            if expect:
                self.assertIn("config/authelia/own-rules.yml", hints[0])

    def _fallback_run(self, healthy_after_recreate):
        class Ui2(Ui):
            def fail(self, text):
                self.warnings.append("FAIL " + text)

            def hint(self, text):
                self.warnings.append("HINT " + text)
        self.ctx.ui = Ui2()
        self.files().mkdir(parents=True)
        (self.files() / "own-rules.yml").write_text("- domain: x.example.org\n  policy: bypass\n")
        stack.stack_files(self.ctx)
        self.ctx.state.answers["_config_hashes"] = dict(self.ctx.extra["config_hashes"])
        calls = []

        def compose(ctx, *args, **kw):
            calls.append(args)
            return ""
        unhealthy = lambda ctx: [] if calls and healthy_after_recreate else ["authelia"]
        clock = iter(range(0, 10_000, 500))
        with mock.patch.object(stack, "_unhealthy", unhealthy), mock.patch.object(stack, "_compose", compose), \
                mock.patch.object(stack.time, "time", lambda: next(clock)), \
                mock.patch.object(stack.time, "sleep", lambda s: None):
            result = stack.stack_healthy(self.ctx)
        return result, calls

    @unittest.skipUnless(HAVE_YAML, "needs python3-yaml")

    def test_authelia_started_again_without_own_rules(self):
        before = self.ctx.state.answers.get("_config_hashes")
        ok, calls = self._fallback_run(True)
        self.assertTrue(ok)
        self.assertEqual(calls, [("up", "-d", "--no-deps", "--force-recreate", "authelia")])
        self.assertNotIn("x.example.org", (self.files() / "configuration.yml").read_text())
        self.assertTrue((self.files() / "own-rules.yml").is_file())  # the user's file stays
        self.assertEqual(self.ctx.extra.get("mine_copies"), [])
        warned = [w for w in self.ctx.ui.warnings if "left out" in w]
        self.assertEqual(len(warned), 1)
        self.assertIn("config/authelia/own-rules.yml", warned[0])
        # the next run sees a different authelia config again and recreates it with the rules
        self.assertNotEqual(self.ctx.state.answers["_config_hashes"]["authelia"],
                            self.ctx.extra["config_hashes"]["authelia"])

    @unittest.skipUnless(HAVE_YAML, "needs python3-yaml")

    def test_rejected_rules_stay_left_out_until_the_file_changes(self):
        import hashlib
        self._fallback_run(True)
        rules = self.files() / "own-rules.yml"
        self.assertEqual(self.ctx.state.answers["_own_rules_rejected"],
                         hashlib.sha256(rules.read_bytes()).hexdigest())
        self.ctx.ui.warnings.clear()
        stack.stack_files(self.ctx)  # next run, same file: no second broken Authelia
        self.assertNotIn("x.example.org", (self.files() / "configuration.yml").read_text())
        self.assertFalse(self.ctx.extra["own_rules_used"])
        self.assertEqual(len([w for w in self.ctx.ui.warnings if "own-rules.yml" in w]), 1)
        rules.write_text("- domain: y.example.org\n  policy: one_factor\n")  # fixed by the user
        stack.stack_files(self.ctx)
        self.assertIn("y.example.org", (self.files() / "configuration.yml").read_text())
        self.assertTrue(self.ctx.extra["own_rules_used"])
        with mock.patch.object(stack, "_unhealthy", lambda ctx: []):
            self.assertTrue(stack.stack_healthy(self.ctx))
        self.assertNotIn("_own_rules_rejected", self.ctx.state.answers)

    @unittest.skipUnless(HAVE_YAML, "needs python3-yaml")

    def test_still_unhealthy_without_own_rules_fails(self):
        ok, calls = self._fallback_run(False)
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(w.startswith("FAIL") for w in self.ctx.ui.warnings))
        self.assertFalse(any("left out" in w for w in self.ctx.ui.warnings))

    def test_no_install_without_own_rules_file(self):
        stack.stack_files(self.ctx)
        self.assertEqual([c for c in self.runs if c[:1] == ["apt-get"]], [])

    def test_users_file_written_when_missing(self):
        self.ctx.extra["changed"] = set()
        stack.stack_files(self.ctx)
        self.assertIn("password", (self.files() / "users.yml").read_text())

    def test_users_file_not_rewritten_without_login_change(self):
        stack.stack_files(self.ctx)
        users = self.files() / "users.yml"
        users.write_text("users: {tim: {password: reset-by-hand}}\n")
        for changed in (set(), {"indexers"}):
            self.ctx.extra["changed"] = changed
            stack.stack_files(self.ctx)
            self.assertEqual(users.read_text(), "users: {tim: {password: reset-by-hand}}\n")
        self.assertEqual(self.ctx.extra.get("mine_copies", []), [])

    def test_users_file_rewritten_when_login_changed(self):
        stack.stack_files(self.ctx)
        users = self.files() / "users.yml"
        users.write_text("users: {}\n")
        for changed in ({"auth"}, {"*"}):
            users.write_text("users: {}\n")
            self.ctx.extra["changed"] = changed
            stack.stack_files(self.ctx)
            self.assertIn("password", users.read_text())

    def test_config_hashes_stable_when_users_file_skipped(self):
        stack.stack_files(self.ctx)
        first = self.ctx.extra["config_hashes"]
        self.ctx.extra["changed"] = set()
        stack.stack_files(self.ctx)
        self.assertEqual(self.ctx.extra["config_hashes"], first)


if __name__ == "__main__":
    unittest.main()
