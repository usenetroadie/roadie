"""Login confirmation codes: Authelia's file notifier writes every "mail" to notification.txt."""

import re
from datetime import datetime, timezone
from pathlib import Path

from roadie.i18n import t
from roadie.ops.common import Env
from roadie.ui import Prompter

_DATE = re.compile(r"^Date: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\S* ([+-]\d{4})", re.M)
_CODE = re.compile(r"^-{10,}\s*\n\s*\n([A-Za-z0-9]{6,12})\s*\n\s*\n-{10,}", re.M)
_LINK = re.compile(r"^(https://\S+)$", re.M)


def latest(path: Path) -> tuple[str, datetime] | None:
    """The code (or, for e.g. password resets, the link) of the newest message, with its time."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    blocks = re.split(r"(?m)^(?=Date: )", text)
    for block in reversed(blocks):
        date = _DATE.search(block)
        if not date:
            continue
        found = _CODE.search(block)
        if found:
            value = found.group(1)
        else:
            links = [m for m in _LINK.findall(block) if "/revoke/" not in m]
            if not links:
                continue
            value = links[0]
        when = datetime.strptime(date.group(1) + date.group(2), "%Y-%m-%d %H:%M:%S%z")
        return value, when
    return None


def show(env: Env, ui: Prompter) -> int:
    found = latest(env.paths.stack_dir / "data/authelia/notification.txt")
    if not found:
        ui.warn(t("authcode.none"))
        return 0
    value, when = found
    age = max(0, int((datetime.now(timezone.utc) - when).total_seconds() // 60))
    ui.info(t("authcode.link" if value.startswith("https://") else "authcode.code"))
    ui.copyable(value)
    ui.hint(t("authcode.age", time=when.astimezone().strftime("%Y-%m-%d %H:%M"), minutes=age))
    if age > 5:
        ui.hint(t("authcode.old"))
    return 0
