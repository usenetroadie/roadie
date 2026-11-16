"""Own domain: do the wizard's host names point at this server?"""

import ipaddress
import re
import socket
import subprocess

from roadie.checks import CheckResult
from roadie.checks.http import HttpError, http_get

HOSTS = ("aiostreams", "auth", "prowlarr")
# Cloudflare shows the address a request comes from. Needed behind NAT (e.g. Oracle Cloud):
# there the server's own network card only has a private address.
TRACE = "https://1.1.1.1/cdn-cgi/trace"


def names(domain: str) -> list[str]:
    return [f"{h}.{domain}" for h in HOSTS]


def public_ipv4() -> str | None:
    try:
        text = http_get(TRACE, timeout=10).text()
    except HttpError:
        return None
    m = re.search(r"^ip=(\S+)$", text, re.M)
    try:
        return str(ipaddress.IPv4Address(m.group(1))) if m else None
    except ValueError:
        return None


def local_ipv4() -> set[str]:
    try:
        own = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        own = []
    return {a for a in own if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", a)}


def resolve(name: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError, OSError):
        return set()


def check_domain(domain: str, mine: set[str] | None = None, lookup=resolve, public: str | None = None) -> CheckResult:
    """mine: the server's addresses (its own ones and the one it is seen from)."""
    if mine is None:
        public = public_ipv4()
        mine = local_ipv4() | ({public} if public else set())
    ip = public or ", ".join(sorted(mine)) or "?"  # what the user's A records should say
    missing, wrong = [], {}
    for name in names(domain):
        found = lookup(name)
        if not found:
            missing.append(name)
        elif mine and not found & mine:
            wrong[name] = ", ".join(sorted(found))
    if missing:
        return CheckResult(False, "check.domain.missing", {"names": ", ".join(missing), "ip": ip})
    if wrong:
        return CheckResult(False, "check.domain.wrong", {"names": ", ".join(wrong), "found": next(iter(wrong.values())),
                                                         "ip": ip})
    return CheckResult(True, "check.domain.ok", {"domain": domain})
