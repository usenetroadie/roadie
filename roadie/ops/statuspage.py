"""The status page status.<domain>: one static HTML file behind the login, written by the
watcher every 5 minutes. No script, nothing secret: services, known problems, last backup,
updates and the week in numbers - for everyone who does not want to open a terminal."""

import html
import time

from roadie import __version__
from roadie.answers import Answers
from roadie.i18n import t
from roadie.ops import doctor, selfupdate
from roadie.ops.common import Env
from roadie.paths import atomic_write
from roadie.redact import Redactor

SERVICE = "status"
FILE = "data/status/index.html"
REFRESH_SECONDS = 300
STYLE = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--text:#1c2330;--dim:#667085;--ok:#1a7f4b;--bad:#b54708;--line:#e4e7ec}
@media(prefers-color-scheme:dark){:root{--bg:#11151c;--card:#1a2029;--text:#e6e9ef;--dim:#98a2b3;--ok:#4ade80;--bad:#fbbf24;--line:#2a3140}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,sans-serif}
main{max-width:40rem;margin:0 auto;padding:1.25rem}h1{font-size:1.25rem;margin:.5rem 0 1rem}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 .5rem}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem 1.1rem;margin-bottom:.9rem}
.big{font-size:1.15rem;font-weight:600}.ok{color:var(--ok)}.bad{color:var(--bad)}
ul{list-style:none;margin:0;padding:0}li{padding:.3rem 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
li:first-child{border-top:0}.fix{color:var(--dim);font-size:.9rem;display:block}
.row{display:flex;justify-content:space-between;gap:1rem}footer{color:var(--dim);font-size:.85rem;text-align:center;padding:.5rem}
"""


def _problems(env: Env) -> list[tuple[str, str | None]]:
    """(text, how to fix it) for every problem the watcher has reported."""
    out = []
    for p in env.load_json("watch.json").get("problems", {}).values():
        if p.get("notified_at") and p.get("key"):
            fix = t(p["key"] + ".fix")
            out.append((t(p["key"], **p.get("params", {})), None if fix == p["key"] + ".fix" else fix))
    return out


def _update_line(env: Env) -> str:
    tag = env.load_json("watch.json").get("selfupdate", {}).get("notified")
    if tag and selfupdate.is_newer(tag, __version__):
        return t("page.update_available", version=tag.removeprefix("v"))
    return t("page.up_to_date", version=__version__)


def render(env: Env, now: float | None = None) -> str:
    now = time.time() if now is None else now
    redact = Redactor(env.secrets.values())  # problem texts quote names from outside
    esc = lambda text: html.escape(redact(str(text)))  # noqa: E731
    services = env.services()
    problems = _problems(env)
    good = [s for s in services if s.get("State") == "running" and s.get("Health") in ("healthy", "", None)]
    fine = not problems and len(good) == len(services) and bool(services)

    def section(title: str, body: str) -> str:
        return f"<section><h2>{esc(title)}</h2>{body}</section>"

    def items(lines: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"

    parts = [f'<section><div class="big {"ok" if fine else "bad"}">'
             f'{esc(t("page.all_fine") if fine else t("page.needs_look"))}</div></section>']
    if problems:
        parts.append(section(t("page.t.problems"), items(
            [esc(text) + (f'<span class="fix">{esc(fix)}</span>' if fix else "") for text, fix in problems])))
    parts.append(section(t("page.t.services"), items(
        [f'<div class="row"><span>{esc(s.get("Service", "?"))}</span><span class="{"ok" if s in good else "bad"}">'
         f'{esc(t("page.running") if s in good else t("page.not_running"))}</span></div>' for s in services])))
    providers = (Answers.restore(env.state, env.secrets).data.get("usenet") or {}).get("providers") or []
    week = doctor.usage_lines(doctor.usage(env.paths.stack_dir / "data/aiostreams/db.sqlite", now), providers)
    parts.append(section(t("doctor.t.week"), items(
        [f'<span class="{"bad" if level == doctor.WARN else ""}">{esc(text)}</span>' for level, text in week])))
    backup = env.load_json("backup.json")
    backup_line = (t("page.backup", time=str(backup.get("time", "")).replace("T", " ")) if backup.get("file")
                   else t("status.no_backup"))
    parts.append(section(t("page.t.care"), items([esc(backup_line), esc(_update_line(env))])))
    from roadie.ops.backup import published
    offered = published(env)
    try:
        mb = max(1, round(offered.stat().st_size / 1_000_000)) if offered else 0
    except OSError:  # removed by a backup running right now
        offered = None
    if offered:
        parts.append(section(t("page.t.backup_download"), items([
            f'<a href="backup/{html.escape(offered.name, quote=True)}" download>{esc(offered.name)}</a> ({mb} MB)'
            f'<span class="fix">{esc(t("page.backup_download_help"))}</span>'])))
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    return (f'<!doctype html><html lang="{esc(t("page.lang"))}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}"><meta name="robots" content="noindex">'
            f"<title>roadie</title><style>{STYLE}</style></head><body><main><h1>roadie</h1>"
            + "".join(parts)
            + f'<footer>{esc(t("page.footer", time=stamp))}</footer></main></body></html>\n')


def write(env: Env, now: float | None = None) -> None:
    """Never fails its caller: the page is a comfort, the watcher's pushes are what counts."""
    try:
        target = env.paths.stack_dir / FILE
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        atomic_write(target, render(env, now), 0o644)
    except Exception as exc:  # e.g. the stack is gone, the disk is full
        import sys
        print(f"status page not written: {type(exc).__name__}", file=sys.stderr)
