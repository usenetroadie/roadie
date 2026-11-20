"""Short status for the login message and `roadie status`. Local data only, fast."""

import sys
import textwrap

from roadie.i18n import t
from roadie.ops.common import Env
from roadie.redact import Redactor
from roadie.ui import terminal_columns

PLAIN_WIDTH = 50  # no terminal (login message): fits a phone


def width() -> int:
    return terminal_columns(sys.stdout, PLAIN_WIDTH) if sys.stdout.isatty() else PLAIN_WIDTH


def _wrap(text: str, first: str, rest: str, cols: int) -> list[str]:
    return textwrap.wrap(text, max(20, cols - 1), initial_indent=first, subsequent_indent=rest,
                         break_long_words=False, break_on_hyphens=False) or [first.rstrip()]


def lines(env: Env, motd: bool = False, cols: int | None = None) -> list[str]:
    cols = cols or width()
    out = ["roadie"]
    from roadie.ops import selfupdate
    notice = selfupdate.notice_lines(env.load_json("watch.json"))
    if notice:  # bold red: an update must not be overlooked at login
        red = (lambda s: f"\033[1;31m{s}\033[0m") if motd or sys.stdout.isatty() else (lambda s: s)
        for i, line in enumerate(notice):
            for part in _wrap(line, "  ", "    " if line.startswith("- ") else "  ", cols):
                out.append(red(part) if i < 2 else part)
    services = env.services()
    good = sum(1 for s in services if s.get("State") == "running" and s.get("Health") in ("healthy", "", None))
    out.append("  " + t("status.services", ok=good, total=len(services)))
    problems = env.load_json("watch.json").get("problems", {})
    reported = [p for p in problems.values() if p.get("notified_at")]
    redact = Redactor(env.secrets.values())  # problem details come from outside: never show a secret
    for p in reported:
        out += _wrap(redact(t(p["key"], **p.get("params", {}))), "  ! ", "    ", cols)
        if t(p["key"] + ".fix") != p["key"] + ".fix":
            out += _wrap(t(p["key"] + ".fix"), "    → ", "      ", cols)
    for name in env.state.answers.get("_indexers_missing") or []:
        out += _wrap(t("status.indexer_missing", name=name), "  ! ", "    ", cols)
    if not env.state.is_done("summary"):  # setup stopped half way: not "no problems"
        out.append("  " + t("status.setup_unfinished"))
    elif not reported and not env.state.answers.get("_indexers_missing"):
        out.append("  " + t("status.no_problems"))
    backup = env.load_json("backup.json")
    restored = env.state.answers.get("_restored") or {}
    if backup:
        out.append("  " + t("status.backup", time=backup["time"].replace("T", " "), file=backup["file"]))
    elif restored.get("file"):  # a new server from a backup: its next own backup comes on Sunday
        out += _wrap(t("status.restored", file=restored["file"], time=restored.get("at", "")), "  ", "  ", cols)
    else:
        out.append("  " + t("status.no_backup"))
    if not env.state.answers.get("_ssh_hardened"):
        out += _wrap(t("status.ssh_not_tested"), "  ", "  ", cols)
    if motd:
        out.append("  " + t("status.menu_hint"))
    return out


def run(env: Env, motd: bool = False) -> int:
    if not motd:  # the menu path: fix Safari passkeys right away (the watcher does it every 5 minutes)
        from roadie.ops.watch import fix_passkeys
        fix_passkeys(env)
    print("\n".join(lines(env, motd=motd)))
    return 0
