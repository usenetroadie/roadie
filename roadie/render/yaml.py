"""Minimal block-style YAML emitter (stdlib only). Strings are always double-quoted
using JSON escaping, which is valid YAML, so values can never change meaning."""

import json
import re

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")  # no leading digit: 123, 1e3 are numbers
# YAML 1.1 words that would become null or a bool as a key (e.g. a login name "null" in users.yml)
_SPECIAL_WORDS = {"null", "true", "false", "yes", "no", "on", "off", "y", "n", "~"}


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)


def _key(k) -> str:
    k = str(k)
    plain = _PLAIN_KEY.match(k) and k.lower() not in _SPECIAL_WORDS
    return k if plain else json.dumps(k, ensure_ascii=False)


def _emit(obj, indent: int, lines: list[str]) -> None:
    pad = "  " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{_key(k)}:")
                _emit(v, indent + 1, lines)
            else:
                lines.append(f"{pad}{_key(k)}: {_empty_or_scalar(v)}")
    else:
        for item in obj:
            if isinstance(item, (dict, list)) and item:
                sub: list[str] = []
                _emit(item, indent + 1, sub)
                first = sub[0].lstrip()
                lines.append(f"{pad}- {first}")
                lines.extend(sub[1:])
            else:
                lines.append(f"{pad}- {_empty_or_scalar(item)}")


def _empty_or_scalar(v) -> str:
    if isinstance(v, dict):
        return "{}"
    if isinstance(v, list):
        return "[]"
    return _scalar(v)


def dump(obj, header: str | None = None) -> str:
    lines: list[str] = [f"# {line}" for line in header.splitlines()] if header else []
    _emit(obj, 0, lines)
    return "\n".join(lines) + "\n"
