"""Generic Newznab check via the caps endpoint (works for any indexer or indexer manager)."""

import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from roadie.answers import normalise_newznab_url
from roadie.checks import CheckResult, network_failure
from roadie.checks.http import HttpError, http_get

AUTH_ERROR_CODES = {"100", "101", "102"}


def check_newznab(url: str, api_key: str, timeout: float = 15) -> CheckResult:
    try:
        base = normalise_newznab_url(url)
    except ValueError:
        return CheckResult(False, "check.newznab.bad_url")
    try:
        resp = http_get(f"{base}/api?{urlencode({'t': 'caps', 'apikey': api_key})}", timeout=timeout)
    except HttpError as e:
        return network_failure(e)
    return check_newznab_body(resp)


def check_newznab_body(resp) -> CheckResult:
    try:
        root = ET.fromstring(resp.body)
    except ET.ParseError:
        return CheckResult(False, "check.newznab.not_newznab", {"status": resp.status})
    if root.tag == "error":
        code = root.get("code", "")
        if code in AUTH_ERROR_CODES:
            return CheckResult(False, "check.newznab.bad_key")
        return CheckResult(False, "check.newznab.error", {"code": code})
    if root.tag != "caps":
        return CheckResult(False, "check.newznab.not_newznab", {"status": resp.status})
    return CheckResult(True, "check.newznab.ok", {"categories": len(root.findall("./categories/category"))})


def _down() -> CheckResult:
    return CheckResult(False, "check.newznab.down")


def probe(url: str, api_key: str, timeout: float = 15) -> tuple[CheckResult, int | None, int | None]:
    """Is it a Newznab indexer, does it take the key, which daily limits does it report?
    caps first (many indexers answer it without checking the key), then one tiny search
    (one API call) that does check the key and carries the limits. An indexer that does not
    answer is "down", not a wrong address."""
    try:
        base = normalise_newznab_url(url)
    except ValueError:
        return CheckResult(False, "check.newznab.bad_url"), None, None
    try:
        resp = http_get(f"{base}/api?{urlencode({'t': 'caps', 'apikey': api_key})}", timeout=timeout)
    except HttpError:
        return _down(), None, None
    if resp.status >= 500:
        return _down(), None, None
    caps = check_newznab_body(resp)
    if not caps.ok:
        return caps, None, None
    try:
        resp = http_get(f"{base}/api?{urlencode({'t': 'search', 'q': 'test', 'limit': 1, 'apikey': api_key})}",
                        timeout=timeout)
    except HttpError:
        return caps, None, None  # caps answered: the key is checked again by Prowlarr
    try:
        root = ET.fromstring(resp.body)
    except ET.ParseError:
        return caps, None, None
    if root.tag == "error" and root.get("code", "") in AUTH_ERROR_CODES:
        return CheckResult(False, "check.newznab.bad_key"), None, None
    api, grab = parse_limits(resp.body)
    return caps, api, grab


def _int_attr(el, name):
    value = el.get(name, "")
    return int(value) if value.isdigit() and int(value) > 0 else None


def parse_limits(body: bytes) -> tuple[int | None, int | None]:
    """Daily (search, download) limits from an <apilimits apimax=… grabmax=…/> element,
    which nZEDb/nntmux-based indexers add to every search answer (NZBHydra reads the same)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None, None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() == "apilimits":
            return _int_attr(el, "apimax"), _int_attr(el, "grabmax")
    return None, None


def detect_limits(url: str, api_key: str, timeout: float = 15) -> tuple[int | None, int | None]:
    """One tiny search (costs one API call); (None, None) when the indexer does not tell."""
    try:
        base = normalise_newznab_url(url)
        resp = http_get(f"{base}/api?{urlencode({'t': 'search', 'q': 'test', 'limit': 1, 'apikey': api_key})}",
                        timeout=timeout)
    except (ValueError, HttpError):
        return None, None
    return parse_limits(resp.body)
