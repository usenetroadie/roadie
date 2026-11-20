"""`roadie doctor`: tests every Usenet provider and every indexer on its own and says in plain
words what is wrong. `roadie report` (weekly timer): the last 7 days as a push message.

The numbers come from AIOStreams' own statistics tables, read-only."""

import datetime
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from roadie.answers import Answers
from roadie.checks import newznab, nntp
from roadie.i18n import t
from roadie.ops import watch
from roadie.ops.common import Env
from roadie.render.envfiles import usenet_providers
from roadie.ui import Prompter

DAYS = 7
MIN_ARTICLES = 5000  # below: too little was streamed to judge a provider
MIN_RELEASES = 20  # below: too few releases for a percentage to mean anything
MANY_FAILED_PERCENT = 15
OK, WARN, INFO = "ok", "warn", "info"
Line = tuple[str, str]  # (level, text)


@dataclass
class Usage:
    stream_bytes: int = 0
    releases: int = 0
    failed: int = 0
    providers: dict[str, dict] = field(default_factory=dict)  # id -> articles, missing, errors
    indexers: dict[str, dict] = field(default_factory=dict)  # name -> ok, failed


def usage(db: Path, now: float, days: int = DAYS) -> Usage | None:
    """None when the statistics cannot be read (no database yet, other AIOStreams version)."""
    since = now - days * 86400
    since_ms = int(since * 1000)
    since_text = datetime.datetime.fromtimestamp(since, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True, timeout=5)
        try:
            u = Usage()
            u.stream_bytes = con.execute("SELECT COALESCE(SUM(bytes), 0) FROM stream_bandwidth WHERE hour_ms >= ?",
                                         (since_ms,)).fetchone()[0]
            u.releases, u.failed = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(status = 'failed'), 0) FROM usenet_library WHERE added_at >= ?",
                (since_text,)).fetchone()
            for pid, articles, missing, errors in con.execute(
                    "SELECT provider_id, SUM(articles), SUM(missing), SUM(errors) FROM usenet_provider_metrics "
                    "WHERE hour_ms >= ? GROUP BY provider_id", (since_ms,)):
                u.providers[pid] = {"articles": articles or 0, "missing": missing or 0, "errors": errors or 0}
            for name, ok, failed in con.execute(
                    "SELECT indexer, SUM(ok + degraded), SUM(failed) FROM usenet_indexer_metrics "
                    "WHERE hour_ms >= ? GROUP BY indexer", (since_ms,)):
                u.indexers[name] = {"ok": ok or 0, "failed": failed or 0}
            return u
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _gb(count: int) -> str:
    return f"{count / 1e9:.1f}"


def usage_lines(u: Usage | None, providers: list[dict]) -> list[Line]:
    """The week in plain words. providers: the answers' provider list (for names and roles)."""
    if u is None:
        return [(INFO, t("doctor.week.unknown"))]
    if not u.stream_bytes and not u.releases:
        return [(INFO, t("doctor.week.nothing"))]
    out = [(INFO, t("doctor.week.streamed", gb=_gb(u.stream_bytes)))]
    if u.releases:
        percent = round(100 * u.failed / u.releases)
        many = u.releases >= MIN_RELEASES and percent >= MANY_FAILED_PERCENT
        out.append((WARN if many else INFO, t("doctor.week.releases", total=u.releases, failed=u.failed, percent=percent)))
        if many:
            out.append((INFO, t("doctor.week.releases_hint")))
    known = {p["id"]: p for p in usenet_providers(providers)}
    primary_missing = sum(n["missing"] for pid, n in u.providers.items() if not known.get(pid, {}).get("isBackup"))
    for pid, n in sorted(u.providers.items()):
        name = known.get(pid, {}).get("host", pid)
        if known.get(pid, {}).get("isBackup"):
            if n["articles"]:
                out.append((OK, t("doctor.week.backup_helped", name=name, articles=n["articles"])))
            continue
        asked = n["articles"] + n["missing"]
        if asked >= MIN_ARTICLES:
            out.append((INFO, t("doctor.week.provider", name=name, percent=f"{100 * n['articles'] / asked:.2f}",
                                missing=n["missing"])))
    for pid, p in known.items():  # a backup that never had to deliver anything says nothing
        if p.get("isBackup") and primary_missing >= 50 and not u.providers.get(pid, {}).get("articles"):
            out.append((WARN, t("doctor.week.backup_useless", name=p["host"], missing=primary_missing)))
    for name, n in sorted(u.indexers.items()):
        if n["failed"] >= 5 and n["failed"] > n["ok"]:
            out.append((WARN, t("doctor.week.indexer_bad", name=name, failed=n["failed"], total=n["ok"] + n["failed"])))
    return out


def service_lines(env: Env) -> list[Line]:
    """What the watcher looks at, right now: services, address from outside, disk, memory."""
    out = []
    for p in watch.collect_problems(env):
        out.append((WARN, t(p.key, **p.params)))
        if t(p.key + ".fix") != p.key + ".fix":
            out.append((INFO, t(p.key + ".fix")))
    return out or [(OK, t("doctor.services_ok"))]


def provider_lines(providers: list[dict]) -> list[Line]:
    out = []
    for p in providers:
        result = nntp.check_nntp(p["host"], p["port"], p["tls"], p["username"], p["password"])
        if result.key in ("check.nntp.refused", "check.nntp.busy"):  # every connection busy streaming
            out.append((OK, f"{p['host']}: {t('check.nntp.busy')}"))
        elif result.ok or result.key == "check.nntp.bad_login":
            out.append((OK if result.ok else WARN, f"{p['host']}: {t(result.key, **result.params)}"))
        else:  # it does not answer: the login is probably fine, the provider is away
            out.append((WARN, f"{p['host']}: {t('doctor.provider_down')}"))
    return out


def indexer_lines(indexers: list[dict]) -> list[Line]:
    out = []
    for ix in indexers:
        result = newznab.check_newznab(ix["url"], ix["api_key"])
        out.append((OK if result.ok else WARN, f"{ix['name']}: {t(result.key, **result.params)}"))
    return out


def _show(ui: Prompter, title: str, lines: list[Line]) -> bool:
    ui.heading(title)
    for level, text in lines:
        {OK: ui.ok, WARN: ui.warn, INFO: ui.info}[level](text)
    return all(level != WARN for level, _ in lines)


def _database(env: Env) -> Path:
    return env.paths.stack_dir / "data/aiostreams/db.sqlite"


def run(env: Env, ui: Prompter) -> int:
    data = Answers.restore(env.state, env.secrets).data
    providers = (data.get("usenet") or {}).get("providers") or []
    good = _show(ui, t("doctor.t.services"), service_lines(env))
    good = _show(ui, t("doctor.t.providers"), provider_lines(providers)) and good
    good = _show(ui, t("doctor.t.indexers"), indexer_lines(data.get("indexers") or [])) and good
    good = _show(ui, t("doctor.t.week"), usage_lines(usage(_database(env), time.time()), providers)) and good
    ui.info("")
    (ui.ok if good else ui.warn)(t("doctor.all_ok" if good else "doctor.problems"))
    return 0 if good else 1


def report(env: Env, dry: bool = False) -> int:
    """Weekly push: the week's numbers, and whether anything needs a look right now."""
    providers = (Answers.restore(env.state, env.secrets).data.get("usenet") or {}).get("providers") or []
    lines = [text for _, text in usage_lines(usage(_database(env), time.time()), providers)]
    open_problems = [p for p in env.load_json("watch.json").get("problems", {}).values() if p.get("notified_at")]
    lines.append(t("report.problems", count=len(open_problems)) if open_problems else t("report.no_problems"))
    text = "\n".join(lines)
    if dry:
        print(text)
        return 0
    env.push(t("report.title"), text, 2)
    return 0
