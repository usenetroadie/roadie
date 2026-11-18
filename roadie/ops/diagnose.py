"""Problem report for a GitHub issue: facts about the installation with every secret,
IP address and the domain removed."""

import platform
import sys

from roadie import __version__, system
from roadie.i18n import t
from roadie.ops.common import Env, disk_used_percent, mem_available_mb
from roadie.paths import atomic_write
from roadie.redact import Redactor

LOG_LINES = 25
ISSUES_URL = "https://github.com/usenetroadie/roadie/issues/new/choose"
FILE_NAME = "roadie-report.txt"


def build(env: Env) -> str:
    redact = Redactor(env.secrets.values(), hide_domain=env.domain)
    redact.add(env.answers.get("domain") or env.answers.get("duckdns", {}).get("subdomain"))
    parts = [
        "### roadie report",
        f"version: {__version__}",
        f"system: {platform.platform()}",
        f"memory available: {mem_available_mb()} MB, disk used: {int(disk_used_percent(env.paths.root))} %",
        f"test mode: {env.test_mode}",
        f"finished steps: {', '.join(env.state.done)}",
    ]
    failure = env.answers.get("_last_failure")
    if failure:
        parts += ["", "### last setup failure", f"{failure.get('at')}: step {failure.get('step')}: {failure.get('message')}"]
    parts += ["", "### services"]
    services = env.services()
    for s in services:
        parts.append(f"{s.get('Service')}: {s.get('State')} {s.get('Health') or ''} ({s.get('Image')})")
    problems = env.load_json("watch.json").get("problems", {})
    parts += ["", "### watcher", ", ".join(sorted(problems)) or "no problems"]
    for s in services:
        name = s.get("Service")
        logs = env.compose("logs", "--no-color", "--no-log-prefix", "--tail", str(LOG_LINES), name, check=False)
        parts += ["", f"### last log lines: {name}", logs.strip()[-4000:]]
    journal = system.run(["journalctl", "-u", "roadie-watch", "-u", "roadie-backup", "-n", "20",
                          "--no-pager", "-o", "cat"], check=False)
    parts += ["", "### maintenance jobs", journal.strip()[-2000:]]
    return redact("\n".join(parts)) + "\n"


def run(env: Env) -> int:
    """Shows the report and saves it in the admin user's home (to download or copy). Nothing
    is sent anywhere. The hints go to stderr, so `diagnose > file` holds only the report."""
    from roadie.ops import backup
    report = build(env)
    print(report)
    user = env.answers.get("admin_user")
    target = backup.backup_dir(env).parent / FILE_NAME
    try:
        atomic_write(target, report, 0o600)
        system.run(["chown", f"{user}:{user}", str(target)], check=False)
        print(t("diagnose.saved", path=target), file=sys.stderr)
    except OSError:
        pass
    print(t("diagnose.share", url=ISSUES_URL), file=sys.stderr)
    return 0
