import io
import tempfile
import unittest
from pathlib import Path

from roadie.engine import Context, Outcome, Step, StepFailed, run_steps
from roadie.paths import Paths
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(Path(self.tmp.name))
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self):
        return Context(
            paths=self.paths,
            state=State(self.paths.state_file),
            secrets=SecretStore(self.paths.secrets_file),
            ui=Prompter(inp=io.StringIO(""), out=io.StringIO()),
            redact=Redactor(),
        )

    def step(self, name, fn=None, verify=None, always=False):
        def run(ctx):
            self.calls.append(name)
            if fn:
                fn(ctx)
        return Step(id=name, title_key=f"step.{name}", run=run, verify=verify, always=always)

    def test_runs_steps_in_order(self):
        run_steps([self.step("a"), self.step("b")], self.ctx())
        self.assertEqual(self.calls, ["a", "b"])

    def test_skipped_step_reported_as_open(self):
        ctx = self.ctx()
        run_steps([self.step("a"),
                   Step("b", "step.b", lambda c: Outcome("skipped", open_key="open.ssh_hardening"), always=True)], ctx)
        last = ctx.ui.out.getvalue().strip().splitlines()[-1]
        self.assertIn("[2/2]", last)
        self.assertTrue(last.startswith(("!", "[!]")), last)
        self.assertEqual(ctx.extra["open_points"], [("step.b", "open.ssh_hardening")])

    def test_ok_and_warn_without_key_add_no_open_points(self):
        ctx = self.ctx()
        run_steps([self.step("a"), Step("b", "step.b", lambda c: Outcome("ok", note="fine")),
                   Step("c", "step.c", lambda c: Outcome("warn", note="look"))], ctx)
        self.assertEqual(ctx.extra.get("open_points", []), [])

    def test_outcome_note_in_step_line(self):
        ctx = self.ctx()
        run_steps([Step("p", "step.p", lambda c: Outcome("ok", note="51 streams"))], ctx)
        self.assertIn("[1/1] step.p: 51 streams (0:00)", ctx.ui.out.getvalue())

    def test_outcome_hints_after_step_line(self):
        ctx = self.ctx()
        run_steps([Step("p", "step.p", lambda c: Outcome("warn", note="none", hints=("why-1",)))], ctx)
        out = ctx.ui.out.getvalue()
        self.assertLess(out.index("step.p: none"), out.index("why-1"))

    def test_ssh_check_before_summary(self):
        from roadie import phases
        ids = [s.id for s in phases.steps(None)]
        self.assertEqual(ids[-2:], ["ssh_hardening", "summary"])
        self.assertLess(ids.index("proof"), ids.index("ssh_hardening"))

    def test_progress_numbers_steps(self):
        ctx = self.ctx()
        run_steps([self.step("a"), self.step("b")], ctx)
        out = ctx.ui.out.getvalue()
        self.assertIn("[1/2] step.a", out)
        self.assertIn("[2/2] step.b", out)

    def test_progress_counts_done_steps_on_resume(self):
        ctx = self.ctx()
        run_steps([self.step("a")], ctx)
        ctx.ui.out = io.StringIO()
        run_steps([self.step("a"), self.step("b")], ctx)
        self.assertIn("[2/2] step.b", ctx.ui.out.getvalue())
        self.assertNotIn("step.a", ctx.ui.out.getvalue())

    def test_resume_skips_done_steps(self):
        run_steps([self.step("a")], self.ctx())
        run_steps([self.step("a"), self.step("b")], self.ctx())
        self.assertEqual(self.calls, ["a", "b"])

    def test_failed_verify_not_marked_done(self):
        with self.assertRaises(StepFailed) as cm:
            run_steps([self.step("a", verify=lambda c: False)], self.ctx())
        self.assertEqual(cm.exception.step_id, "a")
        self.assertFalse(State(self.paths.state_file).is_done("a"))

    def test_exception_message_redacted(self):
        def boom(ctx):
            raise RuntimeError("failed calling https://x/api?apikey=topsecret1")
        with self.assertRaises(StepFailed) as cm:
            run_steps([self.step("a", boom)], self.ctx())
        self.assertNotIn("topsecret1", str(cm.exception))
        self.assertIn("failed calling", str(cm.exception))

    def test_exception_redacts_store_secrets(self):
        ctx = self.ctx()
        ctx.secrets.set("pw", "hunter2hunter2")
        def boom(c):
            raise RuntimeError("bad hunter2hunter2")
        with self.assertRaises(StepFailed) as cm:
            run_steps([self.step("a", boom)], ctx)
        self.assertNotIn("hunter2hunter2", str(cm.exception))

    def test_interrupt_keeps_state(self):
        def interrupt(ctx):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            run_steps([self.step("a"), self.step("b", interrupt)], self.ctx())
        state = State(self.paths.state_file)
        self.assertTrue(state.is_done("a"))
        self.assertFalse(state.is_done("b"))

    def test_always_step_reruns(self):
        run_steps([self.step("a", always=True)], self.ctx())
        run_steps([self.step("a", always=True)], self.ctx())
        self.assertEqual(self.calls, ["a", "a"])

    def test_state_answers_roundtrip(self):
        s = State(self.paths.state_file)
        s.answers["language"] = "de"
        s.mark_done("x")
        s.save()
        s2 = State(self.paths.state_file)
        self.assertEqual(s2.answers["language"], "de")
        self.assertTrue(s2.is_done("x"))


class SshCheckTest(unittest.TestCase):
    """The SSH check step: real login command, honest 'skipped' when no key login was seen."""

    def setUp(self):
        from roadie import i18n
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_check(self, created=False, seen=False):
        from unittest import mock
        from roadie.answers import Answers
        from roadie.phases import host
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        if created:
            state.answers["_ssh_key_created"] = True
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO(""), out=out)
        ui.width = 200
        ui.enter_pressed = lambda timeout: True  # the user presses Enter = skip
        ctx = Context(paths=paths, state=state, secrets=SecretStore(paths.secrets_file), ui=ui,
                      redact=Redactor(), answers=Answers({"admin_user": "streamadmin",
                                                          "duckdns": {"subdomain": "example"}}))
        ctx.extra["interactive"] = True
        with mock.patch.object(host, "key_login_seen", return_value=seen):
            outcome = host.ssh_hardening(ctx)
        return outcome, out.getvalue()

    def test_ssh_instructions_real_command(self):
        _, out = self.run_check()
        self.assertIn("ssh streamadmin@example.duckdns.org\n", out)
        self.assertNotIn("-i ", out)
        self.assertNotIn("<", out)
        _, out = self.run_check(created=True)
        self.assertIn("ssh -i ~/.ssh/roadie streamadmin@example.duckdns.org\n", out)

    def test_skipped_check_is_open_point(self):
        outcome, _ = self.run_check()
        self.assertEqual((outcome.status, outcome.open_key), ("skipped", "open.ssh_hardening"))

    def test_already_hardened_is_ok(self):
        from roadie.answers import Answers
        from roadie.phases import host
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        state.answers["_ssh_hardened"] = True
        ctx = Context(paths=paths, state=state, secrets=SecretStore(paths.secrets_file),
                      ui=Prompter(inp=io.StringIO(""), out=io.StringIO()), redact=Redactor(),
                      answers=Answers({"admin_user": "streamadmin"}))
        from unittest import mock
        with mock.patch.object(host, "_write_sshd_dropin") as write:
            self.assertIsNone(host.ssh_hardening(ctx))
        write.assert_called_once_with(ctx)  # no new login check, but the file is kept current


class HostStepsTest(unittest.TestCase):
    """Steps that run again on every setup run must be idempotent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def run_admin_user(self, key):
        from unittest import mock
        from roadie.answers import Answers
        from roadie.phases import host
        from roadie.render import system as render_system
        paths = Paths(Path(self.tmp.name))
        ctx = Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                      ui=Prompter(inp=io.StringIO(""), out=io.StringIO()), redact=Redactor(),
                      answers=Answers({"admin_user": "streamadmin", "ssh_public_key": key}))
        passwd = f"streamadmin:x:1001:1001::{self.home}:/bin/bash"
        with mock.patch.object(host.system, "ok", return_value=True), \
                mock.patch.object(host.system, "run", side_effect=lambda cmd, **kw: passwd if cmd[0] == "getent" else ""), \
                mock.patch.object(render_system, "SUDO_RULE_DIR", self.tmp.name):
            host.admin_user(ctx)
        return (self.home / ".ssh/authorized_keys").read_text().splitlines()

    def test_admin_user_runs_always(self):
        from roadie.phases import host
        always = {s.id: s.always for s in host.steps()}
        self.assertTrue(always["admin_user"])
        self.assertTrue(always["auto_updates"])

    def test_admin_user_adds_new_key_keeps_old(self):
        old, new = "ssh-ed25519 AAAAold user@a", "ssh-ed25519 AAAAnew user@b"
        self.assertEqual(self.run_admin_user(old), [old])
        self.assertEqual(self.run_admin_user(old), [old])  # unchanged run: no duplicate
        self.assertEqual(self.run_admin_user(new), [old, new])
        self.assertEqual(oct((self.home / ".ssh/authorized_keys").stat().st_mode & 0o777), "0o600")

    def test_auto_updates_writes_current_reboot_time(self):
        from unittest import mock
        from roadie.phases import host
        written = []
        ctx = mock.Mock()
        ctx.extra = {"render": mock.Mock(reboot_time="04:30")}
        with mock.patch.object(host.system, "write_files", side_effect=lambda files, base: written.extend(files)):
            host.auto_updates(ctx)
        self.assertIn('Automatic-Reboot-Time "04:30"', written[0].content)


class HostSafetyTest(unittest.TestCase):
    """Firewall and fail2ban must never lock out the admin (security audit H1, M1, L2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def ctx(self):
        from unittest import mock
        from roadie.answers import Answers
        paths = Paths(Path(self.tmp.name))
        ctx = Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                      ui=Prompter(inp=io.StringIO(""), out=io.StringIO()), redact=Redactor(),
                      answers=Answers({"admin_user": "streamadmin"}))
        ctx.extra["render"] = mock.Mock()
        return ctx

    def test_ssh_ports_from_sshd_and_socket(self):
        from unittest import mock
        from roadie.phases import host
        out = {"sshd": "port 2222\nport 22\nlistenaddress 0.0.0.0:2022\nlistenaddress [::]:2023\n",
               "systemctl": "Listen=0.0.0.0:2200 (Stream)\nListen=[::]:2200 (Stream)\n"}
        with mock.patch.object(host.system, "run", side_effect=lambda cmd, **kw: out[cmd[0]]), \
                mock.patch.object(host, "_RUN_SSHD", Path(self.tmp.name) / "sshd"):
            self.assertEqual(host.ssh_ports(), [22, 2022, 2023, 2200, 2222])

    def test_firewall_allows_real_ssh_port_and_stops_without_one(self):
        from unittest import mock
        from roadie.phases import host
        calls = []
        ctx = self.ctx()
        with mock.patch.object(host, "ssh_ports", return_value=[2222]), \
                mock.patch.object(host.system, "run", side_effect=lambda cmd, **kw: calls.append(cmd) or ""):
            host.firewall(ctx)
        self.assertIn(["ufw", "limit", "2222/tcp"], calls)
        self.assertEqual(ctx.state.answers["_ssh_ports"], [2222])
        calls.clear()
        with mock.patch.object(host, "ssh_ports", return_value=[]), \
                mock.patch.object(host.system, "run", side_effect=lambda cmd, **kw: calls.append(cmd) or ""):
            with self.assertRaises(Exception):
                host.firewall(ctx)
        self.assertNotIn(["ufw", "--force", "enable"], calls)

    def test_client_ip_from_environment_or_parent(self):
        from unittest import mock
        from roadie.phases import host
        with mock.patch.dict(host.os.environ, {"SSH_CONNECTION": "203.0.113.10 51234 192.0.2.1 22"}):
            self.assertEqual(host.client_ip(), "203.0.113.10")
        env = {k: v for k, v in host.os.environ.items() if not k.startswith("SSH_")}
        with mock.patch.dict(host.os.environ, env, clear=True), \
                mock.patch.object(host, "_parent_ssh_vars", return_value=["2001:db8::5 1 2001:db8::1 22"]):
            self.assertEqual(host.client_ip(), "2001:db8::5")
        with mock.patch.dict(host.os.environ, env, clear=True), \
                mock.patch.object(host, "_parent_ssh_vars", return_value=["garbage"]):
            self.assertIsNone(host.client_ip())

    def test_fail2ban_ignores_session_ip_and_uses_ssh_ports(self):
        from unittest import mock
        from roadie.phases import host
        ctx = self.ctx()
        ctx.state.answers["_ssh_ports"] = [2222]
        written = []
        with mock.patch.object(host, "client_ip", return_value="203.0.113.10"), \
                mock.patch.object(host.system, "write_files", side_effect=lambda files, base: written.extend(files)), \
                mock.patch.object(host.system, "run", return_value=""):
            host.fail2ban(ctx)
        jail = next(f.content for f in written if f.path.endswith("jail.d/roadie.conf"))
        self.assertIn("ignoreip = 127.0.0.1/8 ::1 203.0.113.10\n", jail)
        self.assertIn("port = 2222\n", jail)
        self.assertEqual(ctx.state.answers["_trusted_ips"], ["203.0.113.10"])
        self.assertEqual(ctx.paths.stack_dir.stat().st_mode & 0o777, 0o700)  # data of the containers: root only

    def test_trusted_ips_are_few_and_newest(self):
        from roadie.phases import host
        ctx = self.ctx()
        for i in range(8):
            host.trust_ip(ctx, f"203.0.113.{i}")
        host.trust_ip(ctx, "203.0.113.3")  # seen again: becomes newest, no duplicate
        ips = ctx.state.answers["_trusted_ips"]
        self.assertEqual(len(ips), host.MAX_TRUSTED_IPS)
        self.assertEqual(ips[-1], "203.0.113.3")
        self.assertEqual(len(set(ips)), len(ips))
        self.assertFalse(host.trust_ip(ctx, "not an ip"))

    def test_steps_run_again(self):
        from roadie.phases import host
        always = {s.id: s.always for s in host.steps()}
        self.assertTrue(always["firewall"])  # a changed SSH port is allowed on the next run
        self.assertTrue(always["fail2ban"])  # the current session's address is added

    def test_key_login_returns_address(self):
        from unittest import mock
        from roadie.phases import host
        log = "Accepted publickey for streamadmin from 203.0.113.10 port 5 ssh2: ED25519 SHA256:x\n"
        with mock.patch.object(host.system, "run", return_value=log):
            self.assertEqual(host.key_login_seen("streamadmin", "today"), "203.0.113.10")
            self.assertIsNone(host.key_login_seen("other", "today"))

    def test_sudo_rule_checked_before_it_is_active(self):
        from unittest import mock
        from roadie.phases import host
        from roadie.render import system as render_system
        from roadie.system import CommandFailed
        rules = Path(self.tmp.name) / "sudoers.d"
        rules.mkdir()

        def run(cmd, **kw):
            if cmd[0] == "visudo":
                self.assertTrue("." in Path(cmd[-1]).name)  # sudo ignores names with a dot
                self.assertEqual(list(rules.glob("90-*")), [])
                raise CommandFailed(cmd, 1, "syntax error")
            return ""
        ctx = self.ctx()
        ctx.answers.data["ssh_public_key"] = "ssh-ed25519 AAAAx u@h"
        with mock.patch.object(host.system, "ok", return_value=True), \
                mock.patch.object(host.system, "run", side_effect=run), \
                mock.patch.object(render_system, "SUDO_RULE_DIR", str(rules)):
            with self.assertRaises(CommandFailed):
                host.admin_user(ctx)
        self.assertEqual(list(rules.iterdir()), [])


class StepFailedRedactionTest(unittest.TestCase):
    def test_step_failed_message_is_redacted(self):
        from roadie.engine import StepFailed, run_steps
        with tempfile.TemporaryDirectory() as tmp:
            paths = Paths(Path(tmp))
            secrets = SecretStore(paths.secrets_file)
            secrets.set("indexer", "SUPERSECRETKEY")

            def fail(ctx):
                raise StepFailed("prowlarr_indexers", "Prowlarr says: https://x.example/api?apikey=SUPERSECRETKEY " + "x" * 2000)
            ctx = Context(paths=paths, state=State(paths.state_file), secrets=secrets,
                          ui=Prompter(inp=io.StringIO(""), out=io.StringIO()), redact=Redactor(), answers=None)
            with self.assertRaises(StepFailed) as cm:
                run_steps([Step("prowlarr_indexers", "step.prowlarr_indexers", fail)], ctx)
            self.assertNotIn("SUPERSECRETKEY", cm.exception.message)
            self.assertNotIn("SUPERSECRETKEY", str(cm.exception))
            self.assertLessEqual(len(cm.exception.message), 600)
            self.assertEqual(cm.exception.step_id, "prowlarr_indexers")
