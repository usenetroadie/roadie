"""Push messages via ntfy (public ntfy.sh by default, random private topic)."""

import contextlib
import socket
import urllib.error
import urllib.request

DEFAULT_SERVER = "https://ntfy.sh"


@contextlib.contextmanager
def _ipv4_only():
    """Some hosters share one IPv6 range between many customers, which runs into ntfy's
    rate limit. Pushes therefore always go out over IPv4."""
    original = socket.getaddrinfo

    def only_v4(host, port, family=0, *args, **kwargs):
        return original(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = only_v4
    try:
        yield
    finally:
        socket.getaddrinfo = original


def push(topic: str, title: str, message: str, priority: int = 3, server: str = DEFAULT_SERVER) -> bool:
    """Send one push. Returns False on any failure (never raises, never logs the topic)."""
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore") or "roadie",
               "Priority": str(priority)}
    req = urllib.request.Request(f"{server.rstrip('/')}/{topic}", data=message.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with _ipv4_only(), urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ping(url: str) -> bool:
    """Outside monitoring (e.g. healthchecks.io): 'I am alive'."""
    try:
        with _ipv4_only(), urllib.request.urlopen(url, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
