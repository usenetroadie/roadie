"""Shared helpers for the maintenance commands (watch, backup, status, diagnose, update)."""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from roadie import i18n, notify, system
from roadie.answers import base_domain
from roadie.paths import Paths, atomic_write
from roadie.secretstore import SecretStore
from roadie.state import State

# stopped during a backup (only those that run); Traefik keeps running
APP_SERVICES = ("aiostreams", "authelia", "prowlarr", "aiometadata", "aiometadata-redis")
BACKUP_MARKER = "backup-running.json"  # in var_dir while a backup has the apps stopped


@dataclass
class Env:
    paths: Paths
    state: State
    secrets: SecretStore

    @property
    def answers(self) -> dict:
        return self.state.answers

    @property
    def test_mode(self) -> bool:
        return bool(self.answers.get("test_mode"))

    @property
    def domain(self) -> str:
        return base_domain(self.answers)

    @property
    def var_dir(self) -> Path:
        return self.paths.root / "var/lib/roadie"

    def load_json(self, name: str) -> dict:
        path = self.var_dir / name
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def save_json(self, name: str, data: dict) -> None:
        atomic_write(self.var_dir / name, json.dumps(data, indent=1, sort_keys=True) + "\n")

    def push(self, title: str, message: str, priority: int = 3) -> bool:
        topic = self.secrets.get("ntfy_topic")
        if not topic:
            return False
        server = (self.answers.get("notify") or {}).get("ntfy_url") or notify.DEFAULT_SERVER
        return notify.push(topic, title, message, priority, server)

    def compose(self, *args: str, check: bool = True) -> str:
        return system.run(["docker", "compose", *args], cwd=str(self.paths.stack_dir), check=check, timeout=1800)

    def services(self) -> list[dict]:
        out = []
        for line in self.compose("ps", "-a", "--format", "json", check=False).splitlines():
            if line.strip().startswith("{"):
                out.append(json.loads(line))
        return out


def load_env(paths: Paths) -> Env | None:
    """None if the wizard has not been run on this server yet."""
    state = State(paths.state_file)
    if not state.answers:
        return None
    if state.answers.get("language") in i18n.LANGUAGES:
        i18n.set_language(state.answers["language"])
    return Env(paths, state, SecretStore(paths.secrets_file))


def disk_used_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.used / usage.total * 100


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0
