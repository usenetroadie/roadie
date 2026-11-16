"""Phase 0: read-only facts about the server and their evaluation."""

import os
import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field

from roadie.paths import Paths

SYSTEMS = (("ubuntu", "24.04"), ("debian", "12"), ("debian", "13"))  # each one tested end to end
ARCHES = ("x86_64", "aarch64")  # every pinned image is built for amd64 and arm64
REQUIRED_HOSTS = ("github.com", "ghcr.io", "registry-1.docker.io")
# only a warning: not everyone uses them (own domain, own ntfy server), and their own questions
# check them again
OPTIONAL_HOSTS = ("www.duckdns.org", "ntfy.sh")
MIN_RAM_MB, RECOMMENDED_RAM_MB, MIN_DISK_GB = 1800, 3800, 20


@dataclass
class Facts:
    os_id: str
    os_version: str
    arch: str
    is_root: bool
    mem_mb: int
    disk_free_gb: float
    cpu_count: int
    ports_in_use: set[int]
    docker_present: bool
    clock_synced: bool | None
    reachable: dict[str, bool] = field(default_factory=dict)


@dataclass
class Finding:
    level: str  # ok | warn | error
    key: str
    params: dict = field(default_factory=dict)


def parse_os_release(text: str) -> tuple[str, str]:
    values = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', text, re.M))
    return values.get("ID", ""), values.get("VERSION_ID", "")


def parse_meminfo_mb(text: str) -> int:
    m = re.search(r"^MemTotal:\s+(\d+) kB", text, re.M)
    return int(m.group(1)) // 1024 if m else 0


def parse_listen_ports(ss_output: str) -> set[int]:
    """Ports from `ss -Htln` local-address column, IPv4 and IPv6."""
    ports = set()
    for line in ss_output.splitlines():
        cols = line.split()
        if len(cols) >= 4:
            m = re.search(r":(\d+)$", cols[3])
            if m:
                ports.add(int(m.group(1)))
    return ports


def _cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _reachable(host: str) -> bool:
    try:
        with socket.create_connection((host, 443), timeout=5):
            return True
    except OSError:
        return False


def gather_facts(paths: Paths = Paths()) -> Facts:
    """Collect facts without changing anything on the system."""
    def read(rel: str) -> str:
        try:
            return (paths.root / rel).read_text()
        except OSError:
            return ""

    os_id, os_version = parse_os_release(read("etc/os-release"))
    synced = _cmd(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]).strip()
    return Facts(
        os_id=os_id,
        os_version=os_version,
        arch=platform.machine(),
        is_root=os.geteuid() == 0,
        mem_mb=parse_meminfo_mb(read("proc/meminfo")),
        disk_free_gb=shutil.disk_usage(paths.root).free / 1e9,
        cpu_count=os.cpu_count() or 1,
        ports_in_use=parse_listen_ports(_cmd(["ss", "-Htln"])) & {80, 443},
        docker_present=shutil.which("docker") is not None,
        clock_synced={"yes": True, "no": False}.get(synced),
        reachable={h: _reachable(h) for h in REQUIRED_HOSTS + OPTIONAL_HOSTS},
    )


def evaluate(f: Facts, own_install: bool = False, min_disk_gb: float = MIN_DISK_GB) -> list[Finding]:
    """own_install: the wizard already passed preflight here, so Docker and busy web ports
    are its own and must not block a re-run."""
    out: list[Finding] = []

    def add(ok: bool, key: str, level: str = "error", **params):
        out.append(Finding("ok" if ok else level, key, params))

    add((f.os_id, f.os_version) in SYSTEMS, "preflight.os", os=f"{f.os_id} {f.os_version}")
    add(f.arch in ARCHES, "preflight.arch", arch=f.arch)
    add(f.is_root, "preflight.root")
    add(f.mem_mb >= MIN_RAM_MB, "preflight.ram", mb=f.mem_mb, min=MIN_RAM_MB)
    if f.mem_mb >= MIN_RAM_MB:
        add(f.mem_mb >= RECOMMENDED_RAM_MB, "preflight.ram_low", "warn", mb=f.mem_mb)
    add(f.disk_free_gb >= min_disk_gb, "preflight.disk", gb=round(f.disk_free_gb, 1), min=min_disk_gb)
    add(f.cpu_count >= 2, "preflight.cpu", "warn", count=f.cpu_count)
    if not own_install:
        busy = sorted(f.ports_in_use & {80, 443})
        add(not busy, "preflight.ports", ports=", ".join(map(str, busy)))
        add(not f.docker_present, "preflight.docker")
    add(f.clock_synced is not False, "preflight.clock", "warn")
    down = sorted(h for h, ok in f.reachable.items() if not ok)
    needed = [h for h in down if h not in OPTIONAL_HOSTS]
    optional = [h for h in down if h in OPTIONAL_HOSTS]
    add(not needed, "preflight.unreachable", hosts=", ".join(needed))
    if optional:
        add(False, "preflight.unreachable_optional", "warn", hosts=", ".join(optional))
    return out


def has_errors(findings: list[Finding]) -> bool:
    return any(x.level == "error" for x in findings)
