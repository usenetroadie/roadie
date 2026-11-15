"""Server watcher, run every 5 minutes by a systemd timer: collects problems and events,
lets watchlogic decide what is worth a push, and sends it via ntfy."""

import datetime
import json
import socket
import ssl
import sys
import time
from dataclasses import asdict
from pathlib import Path

from roadie import notify, system
from roadie.checks.http import HttpError, http_get
from roadie.i18n import t
from roadie.ops import selfupdate
from roadie.ops.passkey import fix_discoverable
from roadie.ops.common import APP_SERVICES, BACKUP_MARKER, Env, disk_used_percent, mem_available_mb
from roadie.ops.watchlogic import Message, Problem, decide, new_login_ips, parse_accepted_logins

DISK_LIMIT_PERCENT, RAM_LIMIT_MB, TLS_MIN_DAYS = 85, 250, 14
MAX_UNSENT = 20  # pushes that failed are sent again in the next run, at most this many
BACKUP_MAX_SECONDS = 3 * 3600  # an older marker is a leftover (crash), not a running backup


def container_problems(services: list[dict], expected: list[str]) -> list[Problem]:
    seen, out = set(), []
    for s in services:
        name = s.get("Service", "?")
        seen.add(name)
        if s.get("State") != "running":
            out.append(Problem(f"container:{name}", "watch.container_down", {"name": name}))
        elif s.get("Health") not in ("healthy", "", None):
            out.append(Problem(f"container:{name}", "watch.container_unhealthy", {"name": name}))
    for name in expected:
        if name not in seen:
            out.append(Problem(f"container:{name}", "watch.container_down", {"name": name}))
    return out


def tls_days_left(host: str, port: int = 443) -> int | None:
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as s:
                expires = ssl.cert_time_to_seconds(s.getpeercert()["notAfter"])
        return int((expires - time.time()) // 86400)
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


def _outside_problems(env: Env) -> list[Problem]:
    host = f"aiostreams.{env.domain}"
    url_file = env.paths.etc_dir / "addon-url.txt"
    out = []
    try:
        reachable = http_get(url_file.read_text().strip(), timeout=20).status == 200
    except (OSError, HttpError):
        reachable = False
    if not reachable:
        out.append(Problem("outside", "watch.unreachable", {"host": host}))
    days = tls_days_left(host)
    if days is not None and days < TLS_MIN_DAYS:
        out.append(Problem("tls", "watch.tls_expiring", {"days": days}))
    return out


def _indexer_problems(env: Env) -> list[Problem]:
    key = env.secrets.get("prowlarr_api_key")
    cid = env.compose("ps", "-q", "prowlarr", check=False).strip()
    if not key or not cid:
        return []
    try:
        ip = system.container_ip(cid)

        def get(path):
            resp = http_get(f"http://{ip}:9696{path}", headers={"X-Api-Key": key}, timeout=15)
            if resp.status != 200:
                raise ValueError(resp.status)
            return json.loads(resp.body)

        names = {i["id"]: i["name"] for i in get("/api/v1/indexer")}
        statuses = get("/api/v1/indexerstatus")
    except (HttpError, ValueError, KeyError, TypeError, system.CommandFailed):
        return []
    out = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for s in statuses:
        if (s.get("disabledTill") or "") > now:
            name = names.get(s.get("indexerId"), "?")
            out.append(Problem(f"indexer:{name}", "watch.indexer_disabled", {"name": name}))
    return out


def backup_running(env: Env, now: float) -> bool:
    since = env.load_json(BACKUP_MARKER).get("since")
    return isinstance(since, (int, float)) and 0 <= now - since < BACKUP_MAX_SECONDS


def collect_problems(env: Env, now: float | None = None) -> list[Problem]:
    problems = container_problems(env.services(), ["traefik", "authelia", "aiostreams", "prowlarr"])
    if backup_running(env, time.time() if now is None else now):  # the backup stopped them on purpose
        problems = [p for p in problems if p.id.split(":", 1)[1] not in APP_SERVICES]
    if not env.test_mode:
        problems += _outside_problems(env)
    problems += _indexer_problems(env)
    used = disk_used_percent(env.paths.root)
    if used > DISK_LIMIT_PERCENT:
        problems.append(Problem("disk", "watch.disk_full", {"percent": int(used)}))
    free = mem_available_mb()
    if free < RAM_LIMIT_MB:
        problems.append(Problem("ram", "watch.ram_low", {"mb": free}))
    return problems


def collect_events(env: Env, saved: dict, now: float) -> list[Message]:
    """One-off information: reboot, SSH logins, new roadie version. Updates `saved` in place."""
    events = []
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot_id = ""
    if saved.get("boot_id") and boot_id and saved["boot_id"] != boot_id:
        events.append(Message("info", "watch.rebooted"))
    saved["boot_id"] = boot_id
    since = saved.get("last_run")
    if since:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
        journal = system.run(["journalctl", "-u", "ssh", "--since", stamp, "-o", "cat", "--no-pager"], check=False)
        logins, saved["known_ips"] = new_login_ips(saved.get("known_ips", []), parse_accepted_logins(journal))
        for user, ip, is_new in logins:
            events.append(Message("info", "watch.ssh_login_new" if is_new else "watch.ssh_login",
                                  {"user": user, "ip": ip}))
    saved["last_run"] = now
    try:  # optional: must never break the watcher
        new_version = selfupdate.notice(saved, now)
    except Exception as exc:
        print(f"self-update check failed: {type(exc).__name__}", file=sys.stderr)
        new_version = None
    if new_version:
        events.append(new_version)
    return events


def format_messages(messages: list[Message]) -> list[tuple[str, str, int]]:
    """(title, text, priority) per message."""
    out = []
    for m in messages:
        text = t(m.key, **m.params)
        if m.kind == "resolved":
            out.append((t("watch.title.resolved"), text, 3))
        elif m.kind == "info":
            out.append((t("watch.title.info"), text, 4 if m.key == "watch.ssh_login_new" else 3))
        else:
            out.append((t("watch.title.problem"), text, 4))
    return out


def fix_passkeys(env: Env) -> None:
    changed = fix_discoverable(env.paths.stack_dir / "data/authelia/db.sqlite3")
    if changed:
        print(f"passkey: set discoverable on {changed} credential(s)", file=sys.stderr)


def run(env: Env, dry: bool = False) -> int:
    now = time.time()
    if not dry:
        fix_passkeys(env)
    saved = env.load_json("watch.json")
    problems = collect_problems(env, now)
    unsent = [Message(**m) for m in saved.get("unsent", []) if isinstance(m, dict)]
    messages, saved["problems"] = decide(saved.get("problems", {}), problems, now)
    messages = unsent + messages + collect_events(env, saved, now)
    failed = []
    for message, (title, text, priority) in zip(messages, format_messages(messages)):
        if dry:
            print(f"[{title}] {text}")
        elif not env.push(title, text, priority) and env.secrets.get("ntfy_topic"):
            failed.append(asdict(message))  # ntfy not reachable right now: try again next run
    saved["unsent"] = failed[-MAX_UNSENT:]
    if dry:
        for p in problems:
            print(f"problem: {t(p.key, **p.params)}")
        return 0
    env.save_json("watch.json", saved)
    ping_url = env.secrets.get("answer.notify.healthchecks_url")  # the state never holds it
    if ping_url and not problems:
        notify.ping(ping_url)
    return 0
