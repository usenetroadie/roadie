"""Message catalogs (flat JSON, dotted keys) with English fallback."""

import json
from importlib import resources

LANGUAGES = ("en",)
_catalogs: dict[str, dict[str, str]] = {}
_current = "en"


def load(lang: str) -> dict[str, str]:
    if lang not in _catalogs:
        text = resources.files("roadie").joinpath(f"locales/{lang}.json").read_text("utf-8")
        _catalogs[lang] = json.loads(text)
    return _catalogs[lang]


def set_language(lang: str) -> None:
    global _current
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language: {lang}")
    load(lang)
    _current = lang


def language() -> str:
    return _current


def t(key: str, **params) -> str:
    text = load(_current).get(key) or load("en").get(key) or key
    return text.format(**params) if params else text
