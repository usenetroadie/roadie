"""Pure decision logic of the server watcher: which problems are worth a push, and when.

A problem is reported once it was seen in two runs in a row, repeated every 12 hours while
it lasts, and followed by an 'ok again' message when it is gone."""

from dataclasses import dataclass, field

REPORT_AFTER_RUNS = 2
REMIND_SECONDS = 12 * 3600


@dataclass
class Problem:
    id: str  # stable, e.g. "container:aiostreams"
    key: str  # i18n key
    params: dict = field(default_factory=dict)


@dataclass
class Message:
    kind: str  # problem | reminder | resolved | info
    key: str
    params: dict = field(default_factory=dict)


def decide(previous: dict, problems: list[Problem], now: float) -> tuple[list[Message], dict]:
    """previous / returned state: {problem_id: {count, notified_at, key, params}}"""
    messages: list[Message] = []
    state: dict = {}
    for p in problems:
        old = previous.get(p.id, {})
        entry = {"count": old.get("count", 0) + 1, "notified_at": old.get("notified_at"),
                 "key": p.key, "params": p.params}
        if entry["notified_at"] is None:
            if entry["count"] >= REPORT_AFTER_RUNS:
                messages.append(Message("problem", p.key, p.params))
                entry["notified_at"] = now
        elif now - entry["notified_at"] >= REMIND_SECONDS:
            messages.append(Message("reminder", p.key, p.params))
            entry["notified_at"] = now
        state[p.id] = entry
    current = {p.id for p in problems}
    for pid, old in previous.items():
        if pid not in current and old.get("notified_at") is not None:
            messages.append(Message("resolved", old.get("key", pid), old.get("params", {})))
    return messages, state


def new_login_ips(known: list[str], logins: list[tuple[str, str]]) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """logins: (user, ip). Returns [(user, ip, is_new_ip)] and the updated known list."""
    known_set, out = set(known), []
    for user, ip in logins:
        out.append((user, ip, ip not in known_set))
        known_set.add(ip)
    return out, sorted(known_set)


def parse_accepted_logins(journal_text: str) -> list[tuple[str, str]]:
    """Lines like 'Accepted publickey for alice from 203.0.113.10 port 1 ssh2'."""
    out = []
    for line in journal_text.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0] == "Accepted" and parts[2] == "for" and parts[4] == "from":
            out.append((parts[3], parts[5]))
    return out
