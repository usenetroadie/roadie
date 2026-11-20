"""Usenet provider login test (raw NNTP, stdlib only) and backbone data."""

import json
import socket
import ssl
from importlib import resources

from roadie.checks import CheckResult


class _Fail(Exception):
    def __init__(self, result: CheckResult):
        self.result = result


def _net(kind: str) -> CheckResult:
    return CheckResult(False, "check.network", {"kind": kind})


def check_nntp(host: str, port: int, tls: bool, username: str, password: str, timeout: float = 15) -> CheckResult:
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except socket.gaierror:
        return _net("dns")
    except (TimeoutError, socket.timeout):
        return _net("timeout")
    except OSError:
        return _net("connect")
    try:
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if tls else raw
    except (ssl.SSLError, OSError):
        raw.close()
        return _net("tls")
    try:
        with sock:
            f = sock.makefile("rwb")

            def line() -> str:
                data = f.readline(2048)
                if not data:
                    raise _Fail(_net("connect"))
                return data.decode("utf-8", "replace").strip()

            def send(cmd: str) -> str:
                f.write(cmd.encode() + b"\r\n")
                f.flush()
                return line()

            greeting = line()
            if not greeting.startswith(("200", "201")):
                return CheckResult(False, "check.nntp.refused", {"code": greeting[:3]})
            reply = send(f"AUTHINFO USER {username}")
            if reply.startswith("381"):
                reply = send(f"AUTHINFO PASS {password}")
            try:
                send("QUIT")
            except (_Fail, OSError):
                pass
            if reply.startswith("281"):
                return CheckResult(True, "check.nntp.ok")
            if reply.startswith(("481", "482", "502")):
                low = reply.lower()
                if "connection" in low or "too many" in low:  # every connection busy streaming: login is fine
                    return CheckResult(False, "check.nntp.busy")
                return CheckResult(False, "check.nntp.bad_login")
            return CheckResult(False, "check.nntp.unexpected", {"code": reply[:3]})
    except _Fail as e:
        return e.result
    except (TimeoutError, socket.timeout):
        return _net("timeout")
    except ssl.SSLError:
        return _net("tls")
    except OSError:
        return _net("connect")


def provider_data() -> dict:
    text = resources.files("roadie").joinpath("data/usenet_providers.json").read_text("utf-8")
    return json.loads(text)


def known_providers() -> list[dict]:
    return provider_data()["providers"]


def _find(entry: dict) -> dict | None:
    """Provider record for an answer entry: by chosen provider id, else by server host."""
    providers = known_providers()
    pid = entry.get("provider")
    host = (entry.get("host") or "").strip().lower()
    return (next((p for p in providers if pid and p["id"] == pid), None)
            or next((p for p in providers if host and p.get("host") == host), None))


def networks_of(entry: dict) -> list[str]:
    found = _find(entry)
    return list(found["networks"]) if found else []


def _label(entry: dict) -> str:
    found = _find(entry)
    return found["name"] if found else entry.get("host", "?")


def backbone_overview(providers: list[dict]) -> tuple[list[str], list[tuple[str, list[str]]], list[tuple[str, list[str]]]]:
    """For choosing a backup: (backbones of the chosen providers, other backbones, backbones run by
    the same company as a chosen one), each other backbone with every listed brand that sells it.
    Alphabetical, so no brand is put first."""
    data = provider_data()
    nets = data["networks"]
    have = {n for p in providers for n in networks_of(p)}
    owners = {nets[n]["owner"] for n in have}
    brands = {n: sorted((p["name"] for p in data["providers"] if n in p["networks"]), key=str.lower) for n in nets}
    rest = sorted((n for n in nets if n not in have and brands[n]), key=lambda n: nets[n]["name"].lower())
    other = [(nets[n]["name"], brands[n]) for n in rest if nets[n]["owner"] not in owners]
    same = [(nets[n]["name"], brands[n]) for n in rest if nets[n]["owner"] in owners]
    return sorted(nets[n]["name"] for n in have), other, same


def same_backbone_warnings(providers: list[dict]) -> list[tuple[str, str, str, str]]:
    """(backup, primary, level, network name) for each backup that adds no new network.
    level 'network': every network of the backup is one the primary already has.
    level 'owner': different networks, but all run by the primary's owner."""
    data = provider_data()
    nets = data["networks"]
    primary = next((p for p in providers if p.get("role") == "primary"), None)
    if not primary or not (p_nets := set(networks_of(primary))):
        return []
    p_owners = {nets[n]["owner"] for n in p_nets}
    out = []
    for b in providers:
        if b.get("role") != "backup" or not (b_nets := set(networks_of(b))):
            continue
        shared_name = nets[sorted(b_nets)[0]]["name"]
        if b_nets <= p_nets:
            out.append((_label(b), _label(primary), "network", shared_name))
        elif {nets[n]["owner"] for n in b_nets} <= p_owners:
            out.append((_label(b), _label(primary), "owner", shared_name))
    return out
