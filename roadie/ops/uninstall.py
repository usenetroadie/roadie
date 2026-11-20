"""Remove everything the wizard installed - but never what keeps the user able to log in.

Kept on purpose: the admin user and its sudo rule, the SSH hardening, the SSH firewall rule,
the backups folder, Docker and the swap file."""

import os
import re
import shutil
import subprocess
from pathlib import Path

from roadie import system
from roadie.i18n import t
from roadie.ops.common import Env
from roadie.phases.ops import QUIET_MOTD
from roadie.ui import Prompter

UNITS = ["roadie-watch.timer", "roadie-watch.service", "roadie-report.timer", "roadie-report.service", "roadie-backup.timer",
         "roadie-backup.service", "roadie-duckdns.timer", "roadie-duckdns.service"]
FAIL2BAN_FILES = ["etc/fail2ban/jail.d/roadie.conf", "etc/fail2ban/filter.d/roadie-sshd-invalid.conf",
                  "etc/fail2ban/filter.d/roadie-traefik-scanner.conf"]
OTHER_FILES = ["etc/update-motd.d/60-roadie", "etc/apt/apt.conf.d/99zz-roadie"]
DATA_DIRS = ["etc/roadie", "var/lib/roadie"]
WEB_PORTS = ["80/tcp", "443/tcp"]
BIN = "usr/local/bin/roadie"
APP_DIR = "opt/roadie"  # holds the stack and, until the very end, the running program
WORDS = {"löschen": ("loeschen",)}  # ASCII spelling for keyboards without umlauts


def plan(env: Env) -> list[str]:
    return [t(f"uninstall.plan.{key}") for key in
            ("containers", "settings", "timers", "motd", "fail2ban", "firewall", "reboot", "program")]


def kept(env: Env) -> list[str]:
    from roadie.ops import backup
    user = env.answers.get("admin_user") or "-"
    ssh = t("uninstall.kept.ssh") if env.answers.get("_ssh_hardened") else t("uninstall.kept.ssh_password")
    return [t("uninstall.kept.admin", user=user), ssh,
            t("uninstall.kept.backups", folder=str(backup.backup_dir(env))),
            t("uninstall.kept.docker"), t("uninstall.kept.swap")]


def _word_ok(answer: str) -> bool:
    word = t("uninstall.word").lower()
    return answer.strip().lower() in (word, *WORDS.get(word, ()))


def _try(cmd: list[str], cwd: str | None = None) -> bool:
    """Every step tolerates 'already gone' (missing unit, rule, program). False only when
    the command hung, so the step is reported as possibly incomplete."""
    try:
        system.run(cmd, check=False, cwd=cwd, timeout=600)
    except (OSError, system.CommandFailed):
        pass
    except subprocess.TimeoutExpired:
        return False
    return True


def _remove(path: Path) -> bool:
    """Delete a file or folder; a system file the wizard had replaced gets its original back.
    True when the wizard's version is really gone."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        if path.exists() or path.is_symlink():
            return False
        backup = path.with_name(path.name + ".roadie-bak")
        if backup.is_file():
            backup.replace(path)
    except OSError:
        return False
    return True


def _report(ui: Prompter, ok: bool, text: str) -> None:
    if ok:
        ui.ok(text)
    else:
        ui.warn(t("uninstall.step_incomplete", step=text))


def _stop_containers(ui: Prompter, stack: Path) -> bool:
    """The containers must really be gone before their folder is deleted: otherwise they keep
    running and serving the web without anything left to manage them."""
    if not (stack / "compose.yaml").is_file():
        return True
    cwd = str(stack)
    try:
        system.run(["docker", "compose", "down", "-v", "--remove-orphans"], cwd=cwd, timeout=600)
        left = system.run(["docker", "compose", "ps", "-aq"], cwd=cwd, timeout=120).strip()
    except FileNotFoundError:
        return True  # no Docker on this server: there are no containers either
    except (OSError, system.CommandFailed, subprocess.TimeoutExpired):
        left = "error"
    if left:
        ui.fail(t("uninstall.containers_failed"))
        return False
    _try(["docker", "compose", "down", "--rmi", "all"], cwd=cwd)  # images only: may fail without harm
    return True


def _restore_motd(root: Path) -> None:
    """Undo quiet_ubuntu_motd: Ubuntu's own login messages come back."""
    for name in QUIET_MOTD:
        part = root / "etc/update-motd.d" / name
        if part.is_file():
            part.chmod(part.stat().st_mode | 0o111)
    conf = root / "etc/default/motd-news"
    if conf.is_file():
        text = conf.read_text()
        new = re.sub(r"(?m)^ENABLED=0$", "ENABLED=1", text)
        if new != text:
            conf.write_text(new)


def _confirm(env: Env, ui: Prompter) -> bool:
    from roadie.ops import backup
    ui.heading(t("uninstall.heading"))
    ui.info(t("uninstall.goes"))
    for item in plan(env):
        ui.info(f"  - {item}")
    ui.warn(t("uninstall.addons_stop"))
    ui.info(t("uninstall.stays"))
    for item in kept(env):
        ui.info(f"  - {item}")
    if backup.recipient(env) and ui.confirm(t("uninstall.backup_offer"), default=True):
        if backup.run(env) != 0:
            ui.fail(t("uninstall.backup_failed"))
            return False
        ui.hint(t("uninstall.backup_download", folder=str(backup.backup_dir(env))))
    answer = ui.ask(t("uninstall.ask_word", word=t("uninstall.word")), validate=lambda v: None)
    if not _word_ok(answer):
        ui.info(t("uninstall.cancelled"))
        return False
    return True


def run(env: Env, ui: Prompter, confirmed: bool) -> int:
    root = env.paths.root
    if root != Path("/") and os.geteuid() == 0:
        ui.fail(t("uninstall.wrong_root"))  # the commands would change the real system, not the test root
        return 1
    if not confirmed and not _confirm(env, ui):
        return 1
    stack = env.paths.stack_dir
    kept_lines = kept(env)  # before anything is gone (reads the admin user's home)
    done_text, kept_heading = t("uninstall.done"), t("uninstall.stays")
    program_text = t("uninstall.step.program")
    program_failed = t("uninstall.step_incomplete", step=program_text)

    with ui.working(t("uninstall.working")):
        stopped = _stop_containers(ui, stack)
    if not stopped:
        return 1  # nothing deleted yet: running again is safe
    ui.ok(t("uninstall.step.containers"))

    ok = all([_try(["systemctl", "disable", "--now", unit]) for unit in UNITS])
    ok = all([_remove(root / "etc/systemd/system" / unit) for unit in UNITS]) and ok
    ok = _try(["systemctl", "daemon-reload"]) and ok
    for unit in UNITS:
        _try(["systemctl", "reset-failed", unit])
    _report(ui, ok, t("uninstall.step.timers"))

    ok = all([_remove(root / rel) for rel in FAIL2BAN_FILES])
    ok = _try(["systemctl", "try-reload-or-restart", "fail2ban"]) and ok
    _report(ui, ok, t("uninstall.step.fail2ban"))

    # SSH stays allowed and the firewall stays on
    _report(ui, all([_try(["ufw", "--force", "delete", "allow", port]) for port in WEB_PORTS]),
            t("uninstall.step.firewall"))

    ok = all([_remove(root / rel) for rel in OTHER_FILES])
    _restore_motd(root)
    _report(ui, ok, t("uninstall.step.motd"))

    _report(ui, all([_remove(path) for path in [stack, *(root / rel for rel in DATA_DIRS)]]),
            t("uninstall.step.settings"))

    ui.heading(done_text)
    ui.info(kept_heading)
    for line in kept_lines:
        ui.info(f"  - {line}")
    # last: the program itself (this process has everything loaded and can still finish)
    if _remove(root / BIN) & _remove(root / APP_DIR):
        ui.ok(program_text)
    else:
        ui.warn(program_failed)
    return 0
