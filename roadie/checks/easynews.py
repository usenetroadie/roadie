"""Easynews login check, with the same search address AIOStreams' Easynews Search uses."""

import base64
from urllib.parse import urlencode

from roadie.checks import CheckResult, network_failure
from roadie.checks.http import HttpError, http_get

BASE = "https://members.easynews.com"


def check_easynews(username: str, password: str, base: str = BASE) -> CheckResult:
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    url = f"{base}/3.0/api/search?{urlencode({'gps': 'test', 'pby': 1})}"
    try:
        resp = http_get(url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    except HttpError as e:
        return network_failure(e)
    if resp.status == 200:
        return CheckResult(True, "check.easynews.ok")
    if resp.status in (401, 403):
        return CheckResult(False, "check.easynews.bad_login")
    return CheckResult(False, "check.http_status", {"status": resp.status})
