import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from roadie import cli, i18n
from roadie.preflight import Facts

NO_STEPS = lambda answers: []  # noqa: E731 - tests must never run the real system steps
EXAMPLE = Path(__file__).parent.parent / "examples/answers.example.json"


def fake_facts(paths=None):
    return Facts(os_id="ubuntu", os_version="24.04", arch="x86_64", is_root=True, mem_mb=4000,
                 disk_free_gb=50, cpu_count=2, ports_in_use=set(), docker_present=False,
                 clock_synced=True, reachable={"github.com": True})


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()
        i18n.set_language("en")

    def run_cli(self, *argv, gather=fake_facts):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cli.main(list(argv), gather=gather, steps=NO_STEPS)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def answers_file(self, mutate=None):
        data = json.loads(EXAMPLE.read_text())
        if mutate:
            mutate(data)
        path = Path(self.tmp.name) / "answers.json"
        path.write_text(json.dumps(data))
        return str(path)

    def test_second_changing_run_is_refused_while_one_is_going(self):
        from roadie.paths import Paths, exclusive
        busy = "Another roadie run"
        with exclusive(Paths(self.root).lock_file):
            code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file())
            self.assertEqual(code, 2)
            self.assertIn(busy, out)
            code, out = self.run_cli("--root", str(self.root), "status")  # read-only: never blocked
            self.assertNotIn(busy, out)
            for command in ("restart", "people"):  # would start what a running backup stopped
                self.assertIn(command, cli.LOCKED)
        code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file())
        self.assertNotIn(busy, out)

    def test_quiet_backup_waits_for_the_running_one(self):
        import time
        from roadie import paths as paths_mod
        waits = []

        def short(lock, wait):
            waits.append(wait)
            return paths_mod.exclusive(lock, 0.3, 0.1)

        with paths_mod.exclusive(paths_mod.Paths(self.root).lock_file), \
                unittest.mock.patch.object(cli, "exclusive", short):
            started = time.monotonic()
            code, out = self.run_cli("--root", str(self.root), "backup", "--quiet")
        self.assertEqual(waits, [cli.BACKUP_LOCK_WAIT])
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertIn("Another roadie run", out)

    def test_invalid_answers_exit_2(self):
        def bad(d):
            d["language"] = "fr"
            d["admin_user"] = "root"
        code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file(bad))
        self.assertEqual(code, 2)
        self.assertIn("language:", out)
        self.assertIn("admin_user:", out)

    def test_answers_file_not_json_exit_2(self):
        path = Path(self.tmp.name) / "broken.json"
        path.write_text("{nope")
        code, _ = self.run_cli("--root", str(self.root), "setup", "--answers", str(path))
        self.assertEqual(code, 2)

    def test_dry_run_changes_nothing(self):
        code, out = self.run_cli("--root", str(self.root), "setup", "--dry-run", "--answers", self.answers_file())
        self.assertEqual(code, 0, out)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn("example-password", out)

    def test_preflight_error_exit_2(self):
        def busy(paths=None):
            f = fake_facts()
            f.ports_in_use = {80}
            return f
        code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file(), gather=busy)
        self.assertEqual(code, 2)
        self.assertIn("80", out)

    def test_answers_file_cannot_change_admin_user(self):
        (self.root / "etc/roadie").mkdir(parents=True)
        data = json.loads(EXAMPLE.read_text())
        (self.root / "etc/roadie/state.json").write_text(json.dumps({"answers": data, "done": []}))

        def other(d):
            d["admin_user"] = "someoneelse"
        code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file(other))
        self.assertEqual(code, 2)
        self.assertIn(i18n.t("cli.admin_user_changed", old="streamadmin", new="someoneelse"), " ".join(out.split()))
        code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file())
        self.assertEqual(code, 0, out)  # the same name is fine

    def test_answers_language_used(self):
        def de(d):
            d["language"] = "de"
            d["admin_user"] = "root"
        _, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file(de))
        self.assertIn("nicht root", out)

    def test_unexpected_error_is_short_and_redacted(self):
        def boom(paths=None):
            raise RuntimeError("crash with change-me-to-something-long inside")
        code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file(), gather=boom)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("change-me-to-something-long", out)
        self.assertIn("crash with", out)

    def test_help_in_both_languages(self):
        _, en = self.run_cli("--lang", "en", "--help")
        _, de = self.run_cli("--lang", "de", "--help")
        self.assertIn("setup", en)
        self.assertNotEqual(en, de)


class InteractiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.example = json.loads(EXAMPLE.read_text())
        self.asked = 0
        self._orig = cli.questions.ask_all

        def fake_ask_all(ui, checks=None, language=None):
            self.language = language
            self.asked += 1
            return copy.deepcopy(self.example)
        cli.questions.ask_all = fake_ask_all

    def tearDown(self):
        cli.questions.ask_all = self._orig
        self.tmp.cleanup()
        i18n.set_language("en")

    def run_cli(self, text, *argv):
        from roadie.ui import Prompter
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO(text), out=out, secret_reader=lambda p: "")
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(self.root), *argv], gather=fake_facts, ui=ui, steps=NO_STEPS)
        return code, out.getvalue()

    def test_interactive_runs_questionnaire_and_stores(self):
        code, out = self.run_cli("1\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked, 1)
        state = (self.root / "etc/roadie/state.json").read_text()
        self.assertIn("streamadmin", state)
        self.assertNotIn("example-password", state)

    def test_language_asked_before_preflight(self):
        code, out = self.run_cli("2\n")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.language, "de")
        heading = i18n.load("de")["phase.preflight"]
        self.assertLess(out.index("Language / Sprache"), out.index(heading))

    def test_language_not_asked_with_lang_or_on_resume(self):
        code, out = self.run_cli("", "--lang", "en")
        self.assertEqual(code, 0, out)
        self.assertIsNone(self.language)
        self.assertNotIn("Language / Sprache", out)
        code, out = self.run_cli("1\n")  # stored answers: menu "continue", no language question
        self.assertNotIn("Language / Sprache", out)

    def test_test_mode_flag_marks_interactive_answers(self):
        code, out = self.run_cli("1\n", "setup", "--test-mode")
        self.assertEqual(code, 0, out)
        state = json.loads((self.root / "etc/roadie/state.json").read_text())
        self.assertIs(state["answers"]["test_mode"], True)

    def test_resume_uses_stored_answers(self):
        self.run_cli("1\n")
        code, out = self.run_cli("1\n")  # menu: continue
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked, 1)

    def test_rerun_after_install_with_docker_running(self):
        self.run_cli("1\n")
        busy = fake_facts()
        busy.docker_present, busy.ports_in_use = True, {80, 443}
        from roadie.ui import Prompter
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO("1\n"), out=out, secret_reader=lambda p: "")
        code = cli.main(["--root", str(self.root)], gather=lambda paths=None: busy, ui=ui, steps=NO_STEPS)
        self.assertEqual(code, 0, out.getvalue())

    def test_unexpected_error_in_questionnaire_is_short(self):
        def boom(ui, checks=None, language=None):
            raise RuntimeError("bad https://x/api?apikey=leaked123")
        cli.questions.ask_all = boom
        code, out = self.run_cli("1\n")
        self.assertEqual(code, 1)
        self.assertNotIn("leaked123", out)
        self.assertNotIn("Traceback", out)

    def test_answers_file_mode_stores_answers(self):
        from roadie.ui import Prompter
        ui = Prompter(inp=io.StringIO(""), out=io.StringIO())
        cli.main(["--root", str(self.root), "setup", "--answers", str(EXAMPLE)], gather=fake_facts, ui=ui, steps=NO_STEPS)
        self.assertIn("streamadmin", (self.root / "etc/roadie/state.json").read_text())

    def test_change_settings_starts_from_stored_answers(self):
        self.example["people"] = [{"id": "main", "name": "Me", "mode": "main"},
                                  {"id": "anna", "name": "Anna", "mode": "inherit"}]
        self.run_cli("1\n")
        seen = {}

        def fake_change(ui, data, checks=None):
            seen.update(data)
            return data
        with unittest.mock.patch.object(cli.questions, "change_settings", fake_change):
            code, _ = self.run_cli("2\n")  # menu: change settings
        self.assertEqual((code, self.asked), (0, 1))  # the full questionnaire is not asked again
        self.assertEqual(seen["people"], self.example["people"])
        self.assertEqual(seen["auth"]["password"], self.example["auth"]["password"])  # secrets restored


class FinalLineTest(unittest.TestCase):
    """The last line says honestly whether something is still open."""

    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_setup(self, outcome, step=None):
        from roadie.engine import Step
        from roadie.ui import Prompter
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO(""), out=out)
        ui.width = 300
        step = step or Step("ssh_hardening", "step.ssh_hardening", lambda ctx: outcome)
        steps = lambda answers: [step]  # noqa: E731
        code = cli.main(["--root", self.tmp.name, "setup", "--answers", str(EXAMPLE)],
                        gather=fake_facts, ui=ui, steps=steps)
        return code, out.getvalue()

    def test_all_ok_says_done(self):
        code, out = self.run_setup(None)
        self.assertEqual(code, 0)
        self.assertIn(i18n.t("cli.done"), out)
        self.assertNotIn(i18n.t("cli.done_open", count=1), out)

    def test_failed_step_is_remembered_for_diagnose_and_cleared_after_a_good_run(self):
        from roadie.engine import Step, StepFailed
        from roadie.state import State

        def fail(ctx):
            raise StepFailed("stack_up", "aiostreams did not start")
        code, out = self.run_setup(None, Step("stack_up", "step.stack_up", fail))
        self.assertEqual(code, 1)
        self.assertIn("sudo roadie diagnose", out)
        state_file = Path(self.tmp.name) / "etc/roadie/state.json"
        failure = State(state_file).answers["_last_failure"]
        self.assertEqual((failure["step"], failure["message"]), ("stack_up", "aiostreams did not start"))
        self.run_setup(None)
        self.assertNotIn("_last_failure", State(state_file).answers)

    def test_skipped_step_listed_as_open(self):
        from roadie.engine import Outcome
        code, out = self.run_setup(Outcome("skipped", open_key="open.ssh_hardening"))
        self.assertEqual(code, 0)
        self.assertNotIn(i18n.t("cli.done"), out)
        lines = out.strip().splitlines()
        self.assertIn(i18n.t("cli.done_open", count=1), lines[-2])
        self.assertIn(i18n.t("open.ssh_hardening"), out)
        self.assertIn("sudo roadie setup", i18n.t("open.ssh_hardening"))

    def test_proof_without_streams_is_open_point(self):
        from roadie.engine import Step
        from roadie.phases import stack

        class NoStreams:
            def __init__(self, base, timeout=None):
                pass

            def stream_count(self, *args):
                return 0
        with unittest.mock.patch.object(stack, "AioClient", NoStreams), \
                unittest.mock.patch.object(stack, "_container_url", return_value="http://10.0.0.2:3000"):
            code, out = self.run_setup(None, Step("proof", "step.proof", stack.proof, always=True))
        self.assertEqual(code, 0)
        self.assertIn(i18n.t("cli.done_open", count=2), out)  # proof + missing TMDB key (none in this test)
        self.assertIn(i18n.t("open.proof"), out)
        self.assertIn(i18n.t("open.tmdb"), out)


class MenuCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        i18n.set_language("en")

    def run_cli(self, text, *argv, root=True):
        from roadie.ui import Prompter
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO(text), out=out, secret_reader=lambda p: "")
        ui.width = 200  # one message = one line
        args = ["--root", str(self.root), *argv] if root else list(argv)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(args, gather=fake_facts, ui=ui, steps=NO_STEPS)
        return code, out.getvalue()

    def test_access_before_setup(self):
        for command in ("access", "zugang", "code", "people", "personen", "help", "hilfe", "restart", "neustart"):
            code, out = self.run_cli("", command)
            self.assertEqual(code, 2, command)
            self.assertEqual(len(out.strip().splitlines()), 1, out)
            self.assertNotIn("Traceback", out)

    def test_not_root_message(self):
        with unittest.mock.patch.object(cli.os, "geteuid", return_value=1000):
            code, out = self.run_cli("", "access", root=False)
        self.assertEqual(code, 2)
        self.assertIn(i18n.t("cli.need_root"), out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_demo_needs_no_root(self):
        with unittest.mock.patch.object(cli.os, "geteuid", return_value=1000), \
                unittest.mock.patch.object(cli, "_demo", return_value=0):
            code, out = self.run_cli("", "demo", root=False)
        self.assertEqual(code, 0)

    def test_finished_setup_opens_menu(self):
        data = json.loads(EXAMPLE.read_text())
        (self.root / "etc/roadie").mkdir(parents=True)
        (self.root / "etc/roadie/state.json").write_text(json.dumps({"answers": data, "done": ["summary"]}))
        code, out = self.run_cli("0\n")
        self.assertEqual(code, 0, out)
        self.assertIn(i18n.t("menu.title"), out)
        self.assertIn(i18n.t("menu.update"), out)


    def test_menu_change_uses_prefilled_flow(self):
        from roadie import menu, questions
        from roadie.answers import Answers
        from roadie.secretstore import SecretStore
        from roadie.state import State
        paths = cli.Paths(self.root)
        paths.state_file.parent.mkdir(parents=True)
        state, secrets = State(paths.state_file), SecretStore(paths.secrets_file)
        Answers.from_dict(json.loads(EXAMPLE.read_text())).store(state, secrets)
        state.mark_done("summary")
        state.save()
        change = [i.key for i in menu.ITEMS].index("change") + 1
        auth = questions.PART_KEYS.index("auth") + 1
        # menu: change -> review: change auth -> Enter keeps name and password -> ok -> back -> exit
        code, out = self.run_cli(f"{change}\n2\n{auth}\n\n1\n\n0\n")
        self.assertEqual(code, 0, out)
        self.assertIn(i18n.t("review.heading"), out)
        self.assertNotIn(i18n.t("q.domain.subdomain"), out)
        self.assertNotIn(i18n.t("q.language"), out)
        restored = Answers.restore(State(paths.state_file), SecretStore(paths.secrets_file))
        self.assertEqual(restored.data["auth"], json.loads(EXAMPLE.read_text())["auth"])

    def test_update_run_is_not_interactive(self):
        from roadie.engine import Step
        from roadie.ops import backup, common, update
        data = json.loads(EXAMPLE.read_text())
        (self.root / "etc/roadie").mkdir(parents=True)
        (self.root / "etc/roadie/state.json").write_text(json.dumps({"answers": data, "done": ["summary"]}))
        seen = {}
        steps = lambda answers: [Step("summary", "step.summary", lambda ctx: seen.update(ctx.extra), always=True)]
        env = common.load_env(cli.Paths(self.root))
        from roadie.ui import Prompter
        ui = Prompter(inp=io.StringIO(""), out=io.StringIO())
        with unittest.mock.patch.object(update, "plan", return_value=[("traefik", "traefik:v1", "traefik:v2")]), \
                unittest.mock.patch.object(backup, "recipient", return_value=None), \
                unittest.mock.patch.object(cli.subprocess, "run"):
            code = cli._update(env, cli.Paths(self.root), ui, fake_facts, steps)
        self.assertEqual(code, 0)
        self.assertFalse(seen["interactive"])
        self.assertTrue(seen["update"])

    def test_update_without_new_images_still_runs_setup_steps(self):
        from roadie.engine import Step
        from roadie.ops import backup, common, update
        data = json.loads(EXAMPLE.read_text())
        (self.root / "etc/roadie").mkdir(parents=True)
        (self.root / "etc/roadie/state.json").write_text(json.dumps({"answers": data, "done": ["summary"]}))
        seen = {}
        steps = lambda answers: [Step("summary", "step.summary", lambda ctx: seen.update(ctx.extra), always=True)]
        env = common.load_env(cli.Paths(self.root))
        from roadie.ui import Prompter
        ui = Prompter(inp=io.StringIO(""), out=io.StringIO())
        with unittest.mock.patch.object(update, "plan", return_value=[]), \
                unittest.mock.patch.object(backup, "run") as run_backup, \
                unittest.mock.patch.object(cli.subprocess, "run") as sub:
            code = cli._update(env, cli.Paths(self.root), ui, fake_facts, steps)
        self.assertEqual(code, 0)
        self.assertTrue(seen["update"])  # new wizard logic is applied even without new images
        self.assertFalse(seen["interactive"])
        self.assertFalse([c for c in sub.call_args_list if c.args and c.args[0][:3] == ["docker", "image", "rm"]])
        run_backup.assert_not_called()

    def test_update_tries_self_update_first_unless_skipped(self):
        from roadie.ops import common, selfupdate, update
        data = json.loads(EXAMPLE.read_text())
        (self.root / "etc/roadie").mkdir(parents=True)
        (self.root / "etc/roadie/state.json").write_text(json.dumps({"answers": data, "done": ["summary"]}))
        env = common.load_env(cli.Paths(self.root))
        from roadie.ui import Prompter
        ui = Prompter(inp=io.StringIO(""), out=io.StringIO())
        for argv, expected in ((["--lang", "de", "update"], 1), (["--skip-self-update", "update"], 0)):
            args = cli._build_parser().parse_args(argv)
            args.argv = argv
            with unittest.mock.patch.object(selfupdate, "run", return_value=None) as run, \
                    unittest.mock.patch.object(update, "plan", return_value=[]):
                self.assertEqual(cli._update(env, cli.Paths(self.root), ui, fake_facts, lambda answers: [], args), 0)
            self.assertEqual(run.call_count, expected, argv)
            if expected:
                self.assertEqual(run.call_args[0][2], argv)
        args = cli._build_parser().parse_args(["update"])
        args.argv = ["update"]
        with unittest.mock.patch.object(selfupdate, "run", return_value=1), \
                unittest.mock.patch.object(update, "plan") as plan:
            self.assertEqual(cli._update(env, cli.Paths(self.root), ui, fake_facts, None, args), 1)
        plan.assert_not_called()  # restart after the self-update failed: stop with the hint


class SafetyTest(unittest.TestCase):
    def test_real_steps_refuse_without_root_or_on_other_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            from roadie.ui import Prompter
            out = io.StringIO()
            ui = Prompter(inp=io.StringIO(""), out=out)
            code = cli.main(["--root", tmp, "setup", "--answers", str(EXAMPLE)], gather=fake_facts, ui=ui)
            self.assertEqual(code, 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class DemoTest(unittest.TestCase):
    def test_demo_runs_questionnaire_without_changes(self):
        from tests.test_questions import USENET_LINES, USENET_SECRETS
        from roadie.ui import Prompter
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            queue = list(USENET_SECRETS)
            ui = Prompter(inp=io.StringIO("\n".join(USENET_LINES) + "\n"), out=out,
                          secret_reader=lambda p: queue.pop(0))
            code = cli.main(["--root", tmp, "demo"], ui=ui)
            self.assertEqual(code, 0, out.getvalue()[-500:])
            self.assertEqual(list(Path(tmp).iterdir()), [])
            text = out.getvalue()
            self.assertIn("DEMO", text)
            self.assertIn("/stremio/", text)
            self.assertNotIn("usenetpw", text)


class BeginnerCliTest(unittest.TestCase):
    setUp, tearDown, run_cli, answers_file = CliTest.setUp, CliTest.tearDown, CliTest.run_cli, CliTest.answers_file

    def test_demo_banner_german(self):
        from tests.test_questions import USENET_LINES, USENET_SECRETS
        from roadie.ui import Prompter
        lines = list(USENET_LINES)
        lines[0] = "2"
        out = io.StringIO()
        queue = list(USENET_SECRETS)
        ui = Prompter(inp=io.StringIO("\n".join(lines) + "\n"), out=out, secret_reader=lambda p: queue.pop(0))
        code = cli.main(["--root", str(self.root), "demo"], ui=ui)
        self.assertEqual(code, 0, out.getvalue()[-500:])
        text = out.getvalue()
        german = i18n.load("de")["demo.banner"]
        self.assertLess(text.index(german), text.index("Language / Sprache"))  # before the language choice
        self.assertEqual(text.rstrip().splitlines()[-1].strip(), german)  # closing banner in the chosen language

    def test_answers_file_never_prompts(self):
        with unittest.mock.patch("sys.stdin", io.StringIO("")):
            code, out = self.run_cli("--root", str(self.root), "setup", "--answers", self.answers_file())
        self.assertEqual(code, 0, out)
        self.assertNotIn(i18n.t("review.ok_q"), out)
        self.assertNotIn(i18n.t("start.ready"), out)
