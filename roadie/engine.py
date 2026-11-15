"""Resumable step runner: each finished step is recorded, re-runs skip it."""

from dataclasses import dataclass, field
from typing import Any, Callable

from roadie.i18n import t
from roadie.paths import Paths
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter


@dataclass
class Context:
    paths: Paths
    state: State
    secrets: SecretStore
    ui: Prompter
    redact: Redactor
    answers: Any = None
    dry_run: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class Outcome:
    """What a step reports when it did not simply succeed: "warn" (works, look at it later)
    or "skipped". open_key names a text for the list of open points at the end; hints are
    printed below the step line."""
    status: str = "ok"
    note: str | None = None
    open_key: str | None = None
    hints: tuple[str, ...] = ()


@dataclass
class Step:
    id: str
    title_key: str
    run: Callable[[Context], "Outcome | None"]
    verify: Callable[[Context], bool] | None = None
    always: bool = False


class StepFailed(Exception):
    def __init__(self, step_id: str, message: str):
        super().__init__(f"{step_id}: {message}")
        self.step_id = step_id
        self.message = message


def run_steps(steps: list[Step], ctx: Context) -> None:
    for secret in ctx.secrets.values():
        ctx.redact.add(secret)
    for index, step in enumerate(steps, 1):
        if ctx.state.is_done(step.id) and not step.always:
            continue
        try:
            ctx.ui.step_begin(index, len(steps), t(step.title_key))
            outcome = step.run(ctx) or Outcome()
            if step.verify is not None and not step.verify(ctx):
                raise StepFailed(step.id, "verify")
        except KeyboardInterrupt:
            ctx.state.save()
            raise
        except StepFailed as e:  # its message may quote a service's answer: redact it too
            ctx.state.save()
            for secret in ctx.secrets.values():
                ctx.redact.add(secret)
            raise StepFailed(e.step_id, ctx.redact(e.message)[:600]) from None
        except Exception as exc:  # any failure becomes a redacted StepFailed
            ctx.state.save()
            for secret in ctx.secrets.values():
                ctx.redact.add(secret)
            raise StepFailed(step.id, ctx.redact(f"{type(exc).__name__}: {exc}")[:600]) from None
        ctx.ui.step_end(outcome.status, outcome.note)
        for hint in outcome.hints:
            ctx.ui.hint(hint)
        if outcome.status in ("skipped", "warn") and outcome.open_key:
            ctx.extra.setdefault("open_points", []).append((t(step.title_key), outcome.open_key))
        ctx.state.mark_done(step.id)
        ctx.state.save()
