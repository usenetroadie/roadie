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
from roadie.checks import nntp
from roadie.checks.http import HttpError, http_get
from roadie.i18n import t
from roadie.ops import selfupdate, statuspage
from roadie.ops.passkey import fix_discoverable
from roadie.ops.common import APP_SERVICES, BACKUP_MARKER, Env, aiostreams_missed_prowlarr, disk_used_percent, mem_available_mb
from roadie.ops.watchlogic import Message, Problem, decide, new_login_ips, parse_accepted_logins, sftp_downloads
from roadie.render.system import SFTP_LOG_LINE, SSHD_DROPIN

DISK_LIMIT_PERCENT, RAM_LIMIT_MB, TLS_MIN_DAYS = 85, 250, 14
MAX_UNSENT = 20  # pushes that failed are sent again in the next run, at most this many
BACKUP_MAX_SECONDS = 3 * 3600  # an older marker is a leftover (crash), not a running backup
DOWNLOAD_REMIND_SECONDS = 36 * 3600  # the Sunday night backup: a reminder on Monday afternoon


def container_problems(services: list[dict], expected: list[str]) -> list[Problem]:
    seen, out = set(), []
    for s in services:
        name = s.get("Service", "?")
        seen.add(name)
        if s.get("State") != "running":
            out.append(Problem(f"container:{name}", "watch.container_down", {"name": name}))
        elif s.get("Health") not in ("healthy", "starting", "", None):  # starting: just (re)started
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
    # what compose.yaml lists; a stack written by an older roadie simply has fewer services
    expected = env.stack_services() or ["traefik", "authelia", "aiostreams", "prowlarr"]
    problems = container_problems(env.services(), expected)
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


PROVIDER_CHECK_SECONDS = 3600  # one login per hour: the connections belong to streaming


def provider_problems(env: Env, saved: dict, now: float) -> list[Problem]:
    """Does each Usenet provider still take the login? Asked at most once an hour; in between
    the last result stands (a problem must not look solved after 5 minutes)."""
    last = saved.get("provider_check") or {}
    if isinstance(last.get("at"), (int, float)) and 0 <= now - last["at"] < PROVIDER_CHECK_SECONDS:
        return [Problem(**p) for p in last.get("problems", []) if isinstance(p, dict)]
    try:
        from roadie.answers import Answers
        providers = (Answers.restore(env.state, env.secrets).data.get("usenet") or {}).get("providers") or []
    except Exception:  # answers of a broken setup: nothing to check
        providers = []
    fails = dict(last.get("fails") or {})
    found = []
    for p in providers:
        result = nntp.check_nntp(p["host"], p["port"], p["tls"], p["username"], p["password"])
        if result.ok or result.key in ("check.nntp.refused", "check.nntp.busy"):  # busy streaming: fine
            fails.pop(p["host"], None)
            continue
        fails[p["host"]] = fails.get(p["host"], 0) + 1
        if fails[p["host"]] < 2:  # one failed check can be a blip: report the second one in a row
            continue
        key = "watch.provider_login" if result.key == "check.nntp.bad_login" else "watch.provider_down"
        found.append(Problem(f"provider:{p['host']}", key, {"host": p["host"]}))
    saved["provider_check"] = {"at": now, "problems": [p.__dict__ for p in found], "fails": fails}
    return found


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
    try:  # optional as well
        reminder = backup_download_reminder(env, saved, now)
    except Exception as exc:
        print(f"backup download check failed: {type(exc).__name__}", file=sys.stderr)
        reminder = None
    if reminder:
        events.append(reminder)
    return events


def downloaded_via_page(access_log: Path, name: str, seen: dict | None = None) -> bool:
    """A download of the backup from the status page (Traefik access log; 206 = resumed, as
    on iPhones). Reads only what is new since the last run: the log grows with every stream."""
    seen = seen if seen is not None else {}
    found = False
    try:
        with open(access_log, "rb") as f:
            size = f.seek(0, 2)
            pos = seen.get("log_pos", 0)
            f.seek(pos if 0 <= pos <= size else 0)  # a smaller file: it started again
            needle = f"GET /backup/{name} ".encode()
            for raw in f:
                if needle in raw and (b'" 200 ' in raw or b'" 206 ' in raw):
                    found = True
            seen["log_pos"] = f.tell()
    except OSError:
        return False
    return found


def backup_download_reminder(env: Env, saved: dict, now: float) -> Message | None:
    """Once per backup: a reminder when the newest one was not downloaded within 36 hours.
    Counts SFTP downloads (when sshd logs them) and downloads from the status page."""
    from roadie.ops import backup
    access_log = env.paths.stack_dir / "data/traefik/access.log"
    try:
        dropin = env.paths.root / SSHD_DROPIN.lstrip("/")
        sftp_logged = dropin.exists() and SFTP_LOG_LINE in dropin.read_text()
        if not sftp_logged and not access_log.exists():
            return None
        files = sorted(backup.backup_dir(env).glob("roadie-backup-*.tar.age"), key=lambda f: f.stat().st_mtime)
        newest = files[-1] if files else None
        info = newest.stat() if newest else None
    except OSError:
        return None
    if not newest:
        return None
    seen = saved.get("backup_download") or {}
    if seen.get("file") != newest.name:
        seen = {"file": newest.name, "downloaded": False, "reminded": False, "checked": info.st_mtime}
    if not seen["downloaded"]:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seen["checked"]))
        if sftp_logged:
            journal = system.run(["journalctl", "-t", "sftp-server", "--since", stamp, "-o", "cat", "--no-pager"],
                                 check=False)
            seen["downloaded"] = sftp_downloads(journal).get(newest.name, 0) >= info.st_size * 0.99
        seen["downloaded"] = seen["downloaded"] or downloaded_via_page(access_log, newest.name, seen)
        seen["checked"] = now
    saved["backup_download"] = seen
    if seen["downloaded"] or seen["reminded"] or now - info.st_mtime < DOWNLOAD_REMIND_SECONDS:
        return None
    seen["reminded"] = True
    key = "watch.backup_not_downloaded" if sftp_logged else "watch.backup_not_downloaded_page"
    return Message("info", key, {"file": newest.name, "mb": round(info.st_size / 1048576)})


def format_messages(messages: list[Message]) -> list[tuple[str, str, int]]:
    """(title, text, priority) per message."""
    out = []
    for m in messages:
        text = t(m.key, **m.params)
        if m.kind == "resolved":  # good news in its own words, not the old problem sentence
            good = t(m.key + ".resolved", **m.params)
            out.append((t("watch.title.resolved"), good if good != m.key + ".resolved" else t("watch.resolved"), 3))
        elif m.kind == "info":
            out.append((t("watch.title.info"), text, 4 if m.key == "watch.ssh_login_new" else 3))
        else:
            out.append((t("watch.title.problem"), text, 4))
    return out


def restart_if_prowlarr_missed(env: Env, saved: dict) -> bool:
    """After a reboot Docker starts all containers at once, so AIOStreams may ask Prowlarr for
    its indexers too early. Restart it once per start of AIOStreams (never in a loop)."""
    started = aiostreams_missed_prowlarr(env.compose("ps", "-q", "aiostreams", check=False).strip())
    if not started or saved.get("aiostreams_restarted_for") == started:
        return False
    saved["aiostreams_restarted_for"] = started
    env.compose("restart", "aiostreams", check=False)
    print("aiostreams restarted: it started before Prowlarr was ready", file=sys.stderr)
    return True


def fix_passkeys(env: Env) -> None:
    changed = fix_discoverable(env.paths.stack_dir / "data/authelia/db.sqlite3")
    if changed:
        print(f"passkey: set discoverable on {changed} credential(s)", file=sys.stderr)


def run(env: Env, dry: bool = False) -> int:
    now = time.time()
    if not dry:
        fix_passkeys(env)
    saved = env.load_json("watch.json")
    if not dry and not backup_running(env, now):
        try:  # optional, like the provider check below
            restart_if_prowlarr_missed(env, saved)
        except Exception as exc:
            print(f"prowlarr check failed: {type(exc).__name__}", file=sys.stderr)
    problems = collect_problems(env, now)
    try:  # optional: a provider check that breaks must not stop the watcher
        problems += provider_problems(env, saved, now)
    except Exception as exc:
        print(f"provider check failed: {type(exc).__name__}", file=sys.stderr)
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
    if "status" in env.stack_services():
        statuspage.write(env, now)
    ping_url = env.secrets.get("answer.notify.healthchecks_url")  # the state never holds it
    if ping_url and not problems:
        notify.ping(ping_url)
    return 0
