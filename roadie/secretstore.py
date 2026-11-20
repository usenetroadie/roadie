"""Root-only JSON store for generated and entered secrets."""

import json
import secrets as _rand
from pathlib import Path

from roadie.paths import atomic_write

_ALPHANUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_PLAIN = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/l/I: easy to type off a screen


class SecretStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, str] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value
        atomic_write(self.path, json.dumps(self._data, indent=2, sort_keys=True) + "\n")

    def delete(self, name: str) -> None:
        if self._data.pop(name, None) is not None:
            atomic_write(self.path, json.dumps(self._data, indent=2, sort_keys=True) + "\n")

    def generate(self, name: str, nbytes: int = 32, alphabet: str = "alnum") -> str:
        """Return the existing value, or create one. Stable across re-runs."""
        if name in self._data:
            return self._data[name]
        if alphabet == "hex":
            value = _rand.token_hex(nbytes)
        elif alphabet == "urlsafe":
            value = _rand.token_urlsafe(nbytes)
        elif alphabet == "plain":
            value = "".join(_rand.choice(_PLAIN) for _ in range(nbytes + nbytes // 3))
        else:  # alnum: safe in env files, URLs and YAML without quoting
            value = "".join(_rand.choice(_ALPHANUM) for _ in range(nbytes + nbytes // 3))
        self.set(name, value)
        return value

    def names(self) -> list[str]:
        return sorted(self._data)

    def values(self) -> list[str]:
        """Values to redact. The questionnaire draft is a whole JSON document, not a secret
        of its own (its passwords are redacted as answers once the setup runs)."""
        return [v for k, v in self._data.items() if k != "draft"]
