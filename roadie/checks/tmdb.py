"""TMDB key check; accepts a v3 API key or a v4 read access token."""

from roadie.checks import CheckResult, network_failure
from roadie.checks.http import HttpError, http_get

BASE = "https://api.themoviedb.org"


def check_tmdb(key: str, base: str = BASE) -> CheckResult:
    key = key.strip()
    if len(key) > 40:  # v4 read access token (JWT)
        url, headers = f"{base}/3/configuration", {"Authorization": f"Bearer {key}"}
    else:
        url, headers = f"{base}/3/configuration?api_key={key}", {}
    try:
        resp = http_get(url, headers=headers)
    except HttpError as e:
        return network_failure(e)
    if resp.status == 200:
        return CheckResult(True, "check.tmdb.ok")
    if resp.status in (401, 403):
        return CheckResult(False, "check.tmdb.bad_key")
    return CheckResult(False, "check.http_status", {"status": resp.status})
