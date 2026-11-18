"""Setup phases as ordered, resumable steps."""

import subprocess

from roadie.engine import Context, Step
from roadie.phases import host, ops, stack
from roadie.render import build_render_config, load_versions


def prepare(ctx: Context, facts) -> None:
    """Values every step needs: server facts and the render configuration."""
    try:
        tz = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"], capture_output=True,
                            text=True, timeout=10).stdout.strip() or "UTC"
    except (OSError, subprocess.TimeoutExpired):
        tz = "UTC"
    # the disk size, not the free space: free space shrinks after the setup, and changed cache
    # sizes would restart AIOStreams on every update
    disk = facts.disk_total_gb or facts.disk_free_gb
    cfg = build_render_config(ctx.answers, load_versions(), facts.mem_mb, disk, tz)
    cfg.test_mode = bool(ctx.answers.data.get("test_mode"))
    ctx.extra.update(facts=facts, render=cfg)


def steps(answers) -> list[Step]:
    return host.steps() + stack.steps() + ops.steps() + host.final_steps() + stack.final_steps()
