"""Wizard progress and non-secret answers, persisted after every step."""

import json
from pathlib import Path

from roadie.paths import atomic_write


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.done: list[str] = data.get("done", [])
        self.answers: dict = data.get("answers", {})

    def is_done(self, step_id: str) -> bool:
        return step_id in self.done

    def mark_done(self, step_id: str) -> None:
        if step_id not in self.done:
            self.done.append(step_id)

    def save(self) -> None:
        data = {"version": 1, "done": self.done, "answers": self.answers}
        atomic_write(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")
