"""DuckDNS: token/subdomain check (also sets the IP to the caller's address) and DNS check."""

from urllib.parse import urlencode

from roadie.checks import CheckResult, network_failure
from roadie.checks.http import HttpError, http_get

BASE = "https://www.duckdns.org"


def check_duckdns(subdomain: str, token: str, base: str = BASE) -> CheckResult:
    query = urlencode({"domains": subdomain, "token": token, "ip": ""})
    try:
        resp = http_get(f"{base}/update?{query}")
    except HttpError as e:
        return network_failure(e)
    if resp.status == 200 and resp.text().strip() == "OK":
        return CheckResult(True, "check.duckdns.ok", {"domain": f"{subdomain}.duckdns.org"})
    return CheckResult(False, "check.duckdns.rejected")
