"""Which top-level answer parts differ between two runs (so updates keep the user's own edits)."""


def changed_parts(previous: dict | None, current: dict) -> set[str]:
    if previous is None:
        return {"*"}  # fresh setup: everything is new
    keys = {k for k in set(previous) | set(current) if not k.startswith("_")}
    return {k for k in keys if previous.get(k) != current.get(k)}
