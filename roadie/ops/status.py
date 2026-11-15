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
    if not reported:
        out.append("  " + t("status.no_problems"))
    backup = env.load_json("backup.json")
    out.append("  " + (t("status.backup", time=backup["time"].replace("T", " "), file=backup["file"])
                       if backup else t("status.no_backup")))
    if motd:
        out.append("  " + t("status.menu_hint"))
    return out


def run(env: Env, motd: bool = False) -> int:
    if not motd:  # the menu path: fix Safari passkeys right away (the watcher does it every 5 minutes)
        from roadie.ops.watch import fix_passkeys
        fix_passkeys(env)
    print("\n".join(lines(env, motd=motd)))
    return 0
