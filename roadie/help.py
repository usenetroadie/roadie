"""Help pages ("Hilfe und Begriffe"): short, one screen each.

In a body, a line starting with "$ " is a command: printed unwrapped so it can be copied."""

from roadie import access
from roadie.i18n import t
from roadie.ops.common import Env
from roadie.ui import Prompter

TOPICS = ["terms", "backup_download", "trouble", "next_steps", "do_it_yourself", "more_catalogs"]


def _lines(body: str) -> list[str]:
    return [access.COPY + line[2:] if line.startswith("$ ") else line for line in body.split("\n")]


def _params(env: Env) -> dict:
    from roadie.ops import backup
    return {"dir": str(backup.backup_dir(env)), "user": env.answers.get("admin_user") or "ubuntu",
            "host": env.domain}


def body(env: Env | None, topic: str, **params) -> str:
    """A topic's text; parts that do not apply to this server (no backup key) left out."""
    from roadie.ops import backup
    text = t(f"help.{topic}.body", **params)
    if env is None:
        return text
    has_key = bool(backup.recipient(env))
    if topic == "next_steps" and has_key:
        text += "\n" + t("help.next_steps.backup")
    if topic == "backup_download" and not has_key:
        text = t("help.backup_download.no_key") + "\n" + text
    return text


def show(ui: Prompter, topic: str, env: Env | None = None, **params) -> None:
    ui.box(t(f"help.{topic}.title"), _lines(body(env, topic, **params)))


def next_steps(ui: Prompter, env: Env) -> None:
    """End of the setup: like the help page, but step 1 points to the address shown above."""
    lines = _lines(body(env, "next_steps"))
    ui.box(t("help.next_steps.title"), [t("help.next_steps.first_summary")] + lines[1:])


def run(env: Env, ui: Prompter) -> int:
    options = [(topic, t(f"help.{topic}.title")) for topic in TOPICS] + [("back", t("help.back"))]
    topic = ui.choose(t("help.title"), options)
    if topic != "back":
        show(ui, topic, env, **_params(env))
    return 0
