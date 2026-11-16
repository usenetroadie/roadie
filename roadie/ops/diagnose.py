"""Problem report for a GitHub issue: facts about the installation with every secret,
IP address and the domain removed."""

import platform

from roadie import __version__, system
from roadie.ops.common import Env, disk_used_percent, mem_available_mb
from roadie.redact import Redactor

LOG_LINES = 25


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
        "", "### services",
    ]
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
    print(build(env))
    return 0
