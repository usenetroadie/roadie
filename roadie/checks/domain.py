"""Own domain: do the wizard's host names point at this server?"""

import ipaddress
import json
import re
import secrets
import socket
import struct
import subprocess
import urllib.parse

from roadie.checks import CheckResult
from roadie.checks.http import HttpError, http_get

HOSTS = ("aiostreams", "auth", "prowlarr")
OPTIONAL_HOSTS = ("status", "metadata")  # status page; settings page of the own AIOMetadata
# Each shows the address a request comes from. Needed behind NAT (e.g. Oracle Cloud): there
# the server's own network card only has a private address.
IP_SOURCES = ("https://1.1.1.1/cdn-cgi/trace", "https://checkip.amazonaws.com")
DOH = "https://1.1.1.1/dns-query?name={name}&type={type}"
A, NS, CNAME = 1, 2, 5


def names(domain: str, hosts=HOSTS) -> list[str]:
    return [f"{h}.{domain}" for h in hosts]


def parse_ip(text: str) -> str | None:
    """The IPv4 address in an answer of one of IP_SOURCES ("ip=1.2.3.4" line or just the address)."""
    m = re.search(r"^(?:ip=)?(\d+\.\d+\.\d+\.\d+)\s*$", text, re.M)
    try:
        return str(ipaddress.IPv4Address(m.group(1))) if m else None
    except ValueError:
        return None


def public_ipv4() -> str | None:
    for url in IP_SOURCES:
        try:
            ip = parse_ip(http_get(url, timeout=10).text())
        except HttpError:
            continue
        if ip:
            return ip
    return None


def local_ipv4() -> set[str]:
    try:
        own = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        own = []
    return {a for a in own if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", a)}


# DNS ---------------------------------------------------------------------------------------
# A resolver remembers "no such name" for up to an hour. The user often creates the records
# only after the first failed check, so the zone's own name servers are asked directly: they
# always know the current records. Public resolver and system resolver are the fallbacks.

def _doh(name: str, rtype: str) -> dict:
    url = DOH.format(name=urllib.parse.quote(name), type=rtype)
    resp = http_get(url, headers={"Accept": "application/dns-json"}, timeout=5)
    data = json.loads(resp.body) if resp.status == 200 else {}
    return data if isinstance(data, dict) else {}


def _records(data: dict, section: str, rtype: int) -> list[dict]:
    return [r for r in data.get(section) or [] if isinstance(r, dict) and r.get("type") == rtype]


def zone_servers(name: str) -> list[str]:
    """IPv4 addresses of the name servers responsible for `name` (at most 3)."""
    labels = name.rstrip(".").split(".")
    for i in range(len(labels) - 1):  # the name itself, then each parent, never the TLD
        hosts = [r["data"].rstrip(".") for r in _records(_doh(".".join(labels[i:]), "NS"), "Answer", NS)]
        if hosts:
            out = []
            for host in hosts[:3]:
                out += [r["data"] for r in _records(_doh(host, "A"), "Answer", A)][:1]
            return out
    return []


def dns_query(name: str) -> bytes:
    header = struct.pack(">HHHHHH", secrets.randbelow(65536), 0, 1, 0, 0, 0)  # no recursion wanted
    qname = b"".join(bytes([len(p)]) + p for p in name.rstrip(".").encode("idna").split(b".")) + b"\0"
    return header + qname + struct.pack(">HH", A, 1)


def _skip_name(msg: bytes, pos: int) -> int:
    while True:
        length = msg[pos]
        if length >= 0xC0:  # pointer: two bytes, ends the name
            return pos + 2
        if length == 0:
            return pos + 1
        pos += 1 + length


def parse_answer(msg: bytes) -> tuple[set[str], bool]:
    """(A records, answer holds a CNAME) of a DNS answer; raises ValueError on rubbish."""
    try:
        _, flags, questions, answers = struct.unpack(">HHHH", msg[:8])
        if flags & 0x0200:  # truncated
            raise ValueError("truncated")
        pos = 12
        for _ in range(questions):
            pos = _skip_name(msg, pos) + 4
        found, alias = set(), False
        for _ in range(answers):
            pos = _skip_name(msg, pos)
            rtype, _, _, size = struct.unpack(">HHIH", msg[pos:pos + 10])
            pos += 10
            if rtype == A and size == 4:
                found.add(socket.inet_ntoa(msg[pos:pos + 4]))
            alias = alias or rtype == CNAME
            pos += size
        return found, alias
    except (IndexError, struct.error) as e:
        raise ValueError("bad DNS answer") from e


def ask_server(server: str, name: str, timeout: float = 4) -> tuple[set[str], bool]:
    query = dns_query(name)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(query, (server, 53))
        while True:
            msg, peer = s.recvfrom(4096)
            if peer[0] == server and msg[:2] == query[:2]:
                return parse_answer(msg)


def _authoritative(name: str) -> set[str] | None:
    """None: no clear answer (no server reached, or the name is an alias for another zone)."""
    try:
        servers = zone_servers(name)
    except (HttpError, ValueError, KeyError, TypeError, AttributeError):
        return None
    for server in servers:
        try:
            found, alias = ask_server(server, name)
        except (OSError, ValueError, UnicodeError):
            continue
        return None if alias and not found else found
    return None


def resolve(name: str) -> set[str]:
    found = _authoritative(name)
    if found is not None:
        return found
    try:
        found = {r["data"] for r in _records(_doh(name, "A"), "Answer", A)}
        if found:
            return found
    except (HttpError, ValueError, KeyError, TypeError):
        pass
    try:
        return {info[4][0] for info in socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError, OSError):
        return set()


def check_domain(domain: str, mine: set[str] | None = None, lookup=resolve, public: str | None = None,
                 hosts=HOSTS) -> CheckResult:
    """mine: the server's addresses (its own ones and the one it is seen from)."""
    sure = True
    if mine is None:
        public = public_ipv4()
        sure = public is not None  # behind NAT the network card's addresses prove nothing
        mine = local_ipv4() | ({public} if public else set())
    ip = public or ", ".join(sorted(mine)) or "?"  # what the user's A records should say
    missing, wrong = [], {}
    for name in names(domain, hosts):
        found = lookup(name)
        if not found:
            missing.append(name)
        elif mine and not found & mine:
            wrong[name] = ", ".join(sorted(found))
    if missing:
        return CheckResult(False, "check.domain.missing", {"names": ", ".join(missing), "ip": ip})
    if wrong and sure:
        return CheckResult(False, "check.domain.wrong", {"names": ", ".join(wrong), "found": next(iter(wrong.values())),
                                                         "ip": ip})
    if wrong:  # the public address is unknown: do not block someone who did everything right
        return CheckResult(True, "check.domain.unverified", {"domain": domain, "found": next(iter(wrong.values()))},
                           warn=True)
    return CheckResult(True, "check.domain.ok", {"domain": domain})
