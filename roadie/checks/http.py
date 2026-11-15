"""Tiny urllib wrapper whose errors never reveal the URL (it may carry an API key)."""

import http.client
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from roadie import __version__

USER_AGENT = f"roadie/{__version__}"


@dataclass
class HttpResponse:
    status: int
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class HttpError(Exception):
    """kind: dns | connect | timeout | tls | other"""

    def __init__(self, kind: str):
        super().__init__(f"network error ({kind})")
        self.kind = kind


def _kind(reason) -> str:
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, ssl.SSLError):
        return "tls"
    if isinstance(reason, (ConnectionError, OSError)):
        return "connect"
    return "other"


def http_get(url: str, headers: dict | None = None, timeout: float = 10, max_bytes: int = 2_000_000) -> HttpResponse:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    kind = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(resp.status, resp.read(max_bytes))
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, e.read(max_bytes))
    except urllib.error.URLError as e:
        kind = _kind(e.reason)
    except (TimeoutError, socket.timeout):
        kind = "timeout"
    except ssl.SSLError:
        kind = "tls"
    except OSError:
        kind = "connect"
    except (ValueError, http.client.HTTPException):  # e.g. control characters in the URL
        kind = "other"
    raise HttpError(kind)  # raised outside except: no chained exception carrying the URL
