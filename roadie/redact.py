"""Remove secrets, IPs and optionally the domain from text meant for output or reports."""

import re
from typing import Iterable

_PARAM = re.compile(r"(?i)\b(apikey|api_key|token|password|pass|key|secret)=([^&\s\"']+)")
_AUTH = re.compile(r"(?i)(authorization:\s*)(\S+\s+)?\S+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# needs at least one hex letter group pattern typical for IPv6 with '::' or 4+ groups
_IPV6 = re.compile(r"(?i)\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}\b|\b(?:[0-9a-f]{1,4}:)+:[0-9a-f]{0,4}\b")


class Redactor:
    def __init__(self, secrets: Iterable[str] = (), hide_domain: str | None = None):
        self._secrets: set[str] = set()
        self.hide_domain = hide_domain
        for s in secrets:
            self.add(s)

    def add(self, value: str | None) -> None:
        if value and len(value) >= 4:  # never "" or tiny values: they would mangle all output
            self._secrets.add(value)

    def __call__(self, text: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(s, "***")
        text = _PARAM.sub(lambda m: f"{m.group(1)}=***", text)
        text = _AUTH.sub(lambda m: f"{m.group(1)}***", text)
        text = _IPV6.sub(lambda m: m.group(0) if _looks_like_time(m.group(0)) else "[ip6]", text)
        text = _IPV4.sub("[ip]", text)
        if self.hide_domain:
            text = text.replace(self.hide_domain, "[domain]")
        return text


def _looks_like_time(s: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s))
