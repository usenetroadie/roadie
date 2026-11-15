"""Update to the versions that ship with the installed roadie release.

Workflow for the user: install the new roadie release, then run `roadie update`.
Only the pinned, tested versions in data/versions.json are ever installed."""

import re

from roadie.ops.common import Env
from roadie.render import load_versions


def running_images(compose_text: str) -> dict[str, str]:
    """service -> image, read from the generated compose.yaml."""
    out, service = {}, None
    for line in compose_text.splitlines():
        m = re.match(r"^  ([a-z0-9_-]+):\s*$", line)
        if m:
            service = m.group(1)
        m = re.match(r'^    image: "?([^"\s]+)"?', line)
        if m and service:
            out[service] = m.group(1)
    return out


def pending(current: dict[str, str], versions: dict) -> list[tuple[str, str, str]]:
    """(service, old image, new image) for every service whose pinned version changed."""
    out = []
    for service, old in sorted(current.items()):
        v = versions.get(service)
        if v and f"{v['image']}:{v['tag']}" != old:
            out.append((service, old, f"{v['image']}:{v['tag']}"))
    return out


def plan(env: Env) -> list[tuple[str, str, str]]:
    compose_file = env.paths.stack_dir / "compose.yaml"
    if not compose_file.exists():
        return []
    return pending(running_images(compose_file.read_text()), load_versions())
