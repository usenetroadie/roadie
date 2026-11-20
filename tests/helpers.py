"""Local mock servers for tests (bind 127.0.0.1 on an ephemeral port only)."""

import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockHttp:
    """routes: {path: fn(query: dict, headers) -> (status, body: str|bytes[, content_type])}"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []  # (path, query, headers)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_DELETE(self):
                self._handle("DELETE")

            def _handle(self, method):
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                body_in = self.rfile.read(length) if length else b""
                outer.requests.append((url.path, query, dict(self.headers)))
                fn = outer.routes.get(f"{method} {url.path}") or (outer.routes.get(url.path) if method == "GET" else None)
                if fn and method != "GET":
                    result = fn(body_in, self.headers)
                else:
                    result = fn(query, self.headers) if fn else (404, "not found")
                status, body = result[0], result[1]
                ctype = result[2] if len(result) > 2 else "text/plain"
                data = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class MockNntp:
    """Minimal NNTP server. users: {username: password}. greeting: first line sent."""

    def __init__(self, users=None, greeting="200 mock ready", silent=False, auth_reply=None):
        self.users = users or {}
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                if silent:
                    self.rfile.readline()  # wait until the client gives up
                    return
                self.wfile.write((greeting + "\r\n").encode())
                user = None
                for raw in self.rfile:
                    line = raw.decode("utf-8", "replace").strip()
                    cmd = line.split(" ")
                    if line.upper().startswith("AUTHINFO USER"):
                        user = cmd[2]
                        self.wfile.write(b"381 password required\r\n")
                    elif line.upper().startswith("AUTHINFO PASS"):
                        ok = outer.users.get(user) == cmd[2]
                        reply = auth_reply or ("281 ok" if ok else "481 rejected")
                        self.wfile.write((reply + "\r\n").encode())
                    elif line.upper() == "QUIT":
                        self.wfile.write(b"205 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"500 unknown\r\n")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
