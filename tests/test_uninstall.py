import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import cli, i18n, system
from roadie.ops import uninstall
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter

KEPT_FILES = ["etc/ssh/sshd_config.d/01-roadie.conf", "etc/sudoers.d/90-roadie-admin",
              "home/admin/backups/backup-2026-11-15.tar.age", "etc/sysctl.d/60-roadie-swap.conf",
              "etc/apt/sources.list.d/docker.list"]
GONE_FILES = ["opt/roadie/stack/compose.yaml", "opt/roadie/app/roadie/__init__.py",
              "etc/roadie/state.json", "var/lib/roadie/watch.json",
              "etc/systemd/system/roadie-watch.timer", "etc/systemd/system/roadie-duckdns.timer",
              "etc/update-motd.d/60-roadie", "etc/fail2ban/jail.d/roadie.conf",
              "etc/fail2ban/filter.d/roadie-sshd-invalid.conf",
              "etc/fail2ban/filter.d/roadie-traefik-scanner.conf", "etc/apt/apt.conf.d/99zz-roadie",
              "usr/local/bin/roadie"]


class Recorder:
    """Fake system.run: records every command, never touches the system."""

    def __init__(self, home=None):
        self.calls = []
        self.home = home
        self.fail_on = None  # command prefix (str) that fails like a real non-zero exit
        self.containers_left = ""

    def __call__(self, cmd, input=None, check=True, timeout=900, env=None, cwd=None):
        self.calls.append(list(cmd))
        if self.fail_on and " ".join(cmd).startswith(self.fail_on):
            raise system.CommandFailed(cmd, 1, "error")
        if cmd[:3] == ["docker", "compose", "ps"]:
            return self.containers_left
        if cmd[:2] == ["getent", "passwd"] and self.home:
            return f"admin:x:1000:1000::{self.home}:/bin/bash\n"
        return ""

    def changes(self):
        """Everything except the read-only lookup of the admin user's home."""
        return [c for c in self.calls if c[:2] != ["getent", "passwd"]]

    def flat(self):
        return [" ".join(c) for c in self.changes()]


class UninstallBase(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = Paths(self.root)
        for rel in KEPT_FILES + GONE_FILES:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("x\n")
        self.paths.state_file.write_text(json.dumps({"answers": {"admin_user": "admin", "language": "en"},
                                                      "done": ["summary"]}))
        motd = self.root / "etc/update-motd.d"
        for name in ("10-help-text", "50-motd-news"):
            (motd / name).write_text("#!/bin/sh\n")
            (motd / name).chmod(0o644)  # silenced by the wizard
        (self.root / "etc/default").mkdir(parents=True)
        (self.root / "etc/default/motd-news").write_text("ENABLED=0\nURLS=x\n")
        self.rec = Recorder(home=str(self.root / "home/admin"))
        patcher = mock.patch.object(system, "run", self.rec)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(i18n.set_language, "en")

    def env(self):
        return Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))

    def text(self):
        return " ".join(self.out.getvalue().split())  # long lines are wrapped to the terminal width

    def ui(self, text=""):
        self.out = io.StringIO()
        return Prompter(inp=io.StringIO(text), out=self.out, secret_reader=lambda p: "")


class UninstallTest(UninstallBase):
    def test_uninstall_requires_word(self):
        for wrong in ("\n", "yes\n", "delet\n"):
            code = uninstall.run(self.env(), self.ui(wrong), confirmed=False)
            self.assertNotEqual(code, 0)
            self.assertEqual(self.rec.changes(), [])
            for rel in GONE_FILES:
                self.assertTrue((self.root / rel).exists(), rel)
        self.assertEqual(uninstall.run(self.env(), self.ui("  DELETE \n"), confirmed=False), 0)
        self.assertFalse((self.root / "etc/roadie").exists())

    def test_german_word(self):
        i18n.set_language("de")
        self.assertNotEqual(uninstall.run(self.env(), self.ui("delete\n"), confirmed=False), 0)
        self.assertEqual(uninstall.run(self.env(), self.ui("Löschen\n"), confirmed=False), 0)

    def test_uninstall_keeps_access_and_backups(self):
        self.assertEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        for rel in KEPT_FILES:
            self.assertTrue((self.root / rel).exists(), rel)
        for rel in GONE_FILES:
            self.assertFalse((self.root / rel).exists(), rel)
        for d in ("opt/roadie", "etc/roadie", "var/lib/roadie"):
            self.assertFalse((self.root / d).exists(), d)
        calls = self.rec.flat()
        # never touches users, sudo, SSH or the SSH firewall rule; Docker stays installed
        for forbidden in ("userdel", "deluser", "sshd", "apt-get", "ssh ", "22/tcp", "ufw disable", "swapoff"):
            self.assertFalse(any(forbidden in c for c in calls), forbidden)
        self.assertIn("ufw --force delete allow 80/tcp", calls)
        self.assertIn("ufw --force delete allow 443/tcp", calls)
        self.assertTrue(any(c.startswith("docker compose down -v") for c in calls))
        self.assertIn("systemctl disable --now roadie-duckdns.timer", calls)
        self.assertIn("systemctl daemon-reload", calls)
        self.assertTrue(any("fail2ban" in c for c in calls))
        # containers go first, the program itself last
        self.assertTrue(calls[0].startswith("docker compose down"))
        text = self.out.getvalue()
        self.assertIn(str(self.root / "home/admin/backups"), text)
        self.assertIn("Docker", text)
        self.assertIn("swap", text.lower())

    def test_restores_ubuntu_login_messages(self):
        uninstall.run(self.env(), self.ui(), confirmed=True)
        for name in ("10-help-text", "50-motd-news"):
            self.assertTrue((self.root / "etc/update-motd.d" / name).stat().st_mode & 0o100, name)
        self.assertIn("ENABLED=1", (self.root / "etc/default/motd-news").read_text())

    def test_restores_replaced_system_file(self):
        jail = self.root / "etc/fail2ban/jail.d/roadie.conf"
        jail.with_name(jail.name + ".roadie-bak").write_text("original\n")
        uninstall.run(self.env(), self.ui(), confirmed=True)
        self.assertEqual(jail.read_text(), "original\n")

    def test_backup_restore_error_is_reported_not_raised(self):
        jail = self.root / "etc/fail2ban/jail.d/roadie.conf"
        jail.with_name(jail.name + ".roadie-bak").write_text("original\n")
        with mock.patch.object(Path, "replace", side_effect=PermissionError("read-only")):
            self.assertFalse(uninstall._remove(jail))

    def test_unexpected_error_has_no_traceback(self):
        SecretStore(self.paths.secrets_file).set("x", "SUPERSECRETVALUE")
        out = io.StringIO()
        with mock.patch.object(uninstall, "run", side_effect=RuntimeError("boom SUPERSECRETVALUE")), \
                contextlib.redirect_stderr(out):
            code = cli._uninstall(self.paths, self.ui(), True)
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertNotIn("SUPERSECRETVALUE", self.out.getvalue() + out.getvalue())
        self.assertNotIn("Traceback", self.out.getvalue() + out.getvalue())
        self.assertIn("boom", self.out.getvalue())

    def test_tolerates_already_gone(self):
        uninstall.run(self.env(), self.ui(), confirmed=True)

        def failing(cmd, **kw):
            raise system.CommandFailed(cmd, 1, "not found")
        with mock.patch.object(system, "run", failing):
            self.assertEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)

    def test_compose_skipped_without_stack(self):
        (self.root / "opt/roadie/stack/compose.yaml").unlink()
        uninstall.run(self.env(), self.ui(), confirmed=True)
        self.assertFalse(any(c.startswith("docker compose") for c in self.rec.flat()))

    def test_uninstall_flag_skips_question(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["--root", str(self.root), "uninstall", "--yes-delete-everything"],
                            ui=Prompter(inp=io.StringIO(""), out=out))
        self.assertEqual(code, 0, out.getvalue())
        self.assertFalse((self.root / "etc/roadie").exists())
        self.assertFalse((self.root / "usr/local/bin/roadie").exists())

    def test_german_command_and_flag(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["--root", str(self.root), "deinstallieren", "--ja-alles-loeschen"],
                            ui=Prompter(inp=io.StringIO(""), out=out))
        self.assertEqual(code, 0, out.getvalue())
        self.assertFalse((self.root / "opt/roadie").exists())

    def test_offers_backup_when_key_exists(self):
        env = self.env()
        env.state.answers["_age_recipient"] = "age1example"
        with mock.patch("roadie.ops.backup.run", return_value=0) as backup:
            uninstall.run(env, self.ui("y\ndelete\n"), confirmed=False)
        backup.assert_called_once()
        self.assertIn(str(self.root / "home/admin/backups"), self.out.getvalue())

    def test_failed_backup_stops(self):
        env = self.env()
        env.state.answers["_age_recipient"] = "age1example"
        with mock.patch("roadie.ops.backup.run", return_value=1):
            self.assertNotEqual(uninstall.run(env, self.ui("y\ndelete\n"), confirmed=False), 0)
        self.assertTrue((self.root / "etc/roadie").exists())

    def test_no_backup_question_without_key(self):
        with mock.patch("roadie.ops.backup.run") as backup:
            uninstall.run(self.env(), self.ui("delete\n"), confirmed=False)
        backup.assert_not_called()

    def assert_nothing_deleted(self):
        for rel in GONE_FILES:
            self.assertTrue((self.root / rel).exists(), rel)
        self.assertFalse(any(c.startswith(("systemctl", "ufw")) for c in self.rec.flat()))

    def test_failed_compose_down_deletes_nothing(self):
        self.rec.fail_on = "docker compose down"
        self.assertNotEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        self.assert_nothing_deleted()
        self.assertIn("sudo roadie uninstall", self.text())

    def test_remaining_containers_delete_nothing(self):
        self.rec.containers_left = "abc123\n"
        self.assertNotEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        self.assert_nothing_deleted()

    def test_image_removal_failure_does_not_block(self):
        self.rec.fail_on = "docker compose down --rmi"  # only raises with check=True; the image step is tolerant
        self.assertEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        calls = self.rec.flat()
        self.assertIn("docker compose ps -aq", calls)
        self.assertLess(calls.index("docker compose ps -aq"), calls.index("docker compose down --rmi all"))

    def test_no_docker_means_no_containers(self):
        def run(cmd, **kw):
            if cmd[0] == "docker":
                raise FileNotFoundError("docker")
            return self.rec(cmd, **kw)
        with mock.patch.object(system, "run", run):
            self.assertEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        self.assertFalse((self.root / "opt/roadie").exists())

    def test_timeout_reported_not_raised(self):
        def run(cmd, **kw):
            if cmd[0] == "ufw":
                raise subprocess.TimeoutExpired(cmd, 600)
            return self.rec(cmd, **kw)
        with mock.patch.object(system, "run", run):
            self.assertEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        self.assertIn(i18n.t("uninstall.step_incomplete", step=i18n.t("uninstall.step.firewall")), self.text())

    def test_undeletable_path_is_reported(self):
        real = uninstall._remove
        with mock.patch.object(uninstall, "_remove",
                               side_effect=lambda p: False if p.name == "roadie.conf" else real(p)):
            uninstall.run(self.env(), self.ui(), confirmed=True)
        text = self.text()
        self.assertIn(i18n.t("uninstall.step_incomplete", step=i18n.t("uninstall.step.fail2ban")), text)
        self.assertIn(i18n.t("uninstall.step.firewall"), text)

    def test_reset_failed_only_own_units(self):
        uninstall.run(self.env(), self.ui(), confirmed=True)
        resets = [c for c in self.rec.changes() if c[:2] == ["systemctl", "reset-failed"]]
        self.assertTrue(resets)
        for c in resets:
            self.assertEqual(len(c), 3)
            self.assertTrue(c[2].startswith("roadie-"), c)

    def test_program_removed_last(self):
        removed = []
        real = uninstall._remove
        with mock.patch.object(uninstall, "_remove", side_effect=lambda p: removed.append(p) or real(p)):
            uninstall.run(self.env(), self.ui(), confirmed=True)
        self.assertEqual(removed[-2:], [self.root / uninstall.BIN, self.root / uninstall.APP_DIR])

    def test_refuses_test_root_as_real_root(self):
        with mock.patch("os.geteuid", return_value=0):
            self.assertNotEqual(uninstall.run(self.env(), self.ui(), confirmed=True), 0)
        self.assertEqual(self.rec.changes(), [])
        self.assertTrue((self.root / "etc/roadie").exists())

    def test_plan_lists_items(self):
        items = uninstall.plan(self.env())
        self.assertGreaterEqual(len(items), 6)
        text = "\n".join(items)
        for word in ("80", "443", "fail2ban", "roadie"):
            self.assertIn(word, text)
        i18n.set_language("de")
        self.assertNotEqual(uninstall.plan(self.env()), items)

    def test_menu_has_uninstall_last(self):
        from roadie import menu
        self.assertEqual(menu.ITEMS[-1].key, "uninstall")

    def test_menu_ends_after_uninstall(self):
        from roadie import menu
        n = len(menu.visible_items(self.env(), {"change": 1, "update": 1}))
        code = menu.run(self.env(), self.ui(f"{n}\ndelete\n"), actions={"change": lambda e, u: 0,
                                                                        "update": lambda e, u: 0})
        self.assertEqual(code, 0)
        self.assertEqual(self.out.getvalue().count(i18n.t("menu.title")), 1)


if __name__ == "__main__":
    unittest.main()
