"""The answers given so far, kept while the questions are not finished: Ctrl+C or a lost
connection in part 5 does not throw away parts 1 to 4. Kept in the secret store (root only),
because answers hold passwords, and removed as soon as the setup starts."""

import json

from roadie import __version__
from roadie.secretstore import SecretStore

KEY = "draft"


def save(secrets: SecretStore, data: dict, done: list[str]) -> None:
    secrets.set(KEY, json.dumps({"version": __version__, "data": data, "done": list(done)}))


def load(secrets: SecretStore) -> tuple[dict, list[str]] | None:
    """None when there is no draft, or one from another roadie version or a broken one."""
    try:
        draft = json.loads(secrets.get(KEY) or "null")
        if not isinstance(draft, dict) or draft.get("version") != __version__:
            return None
        data, done = draft["data"], draft["done"]
        if not isinstance(data, dict) or not isinstance(done, list) or not done:
            return None
        return data, [str(d) for d in done]
    except (ValueError, KeyError, TypeError):
        return None


def clear(secrets: SecretStore) -> None:
    secrets.delete(KEY)
