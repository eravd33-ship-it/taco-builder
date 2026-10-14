"""The local web server behind the window. Standard library only.

It listens on 127.0.0.1 and is locked down because any web page you visit can
send requests to localhost:
  * the Host header must be 127.0.0.1/localhost with our port (blocks DNS rebinding),
  * every /api call needs a per-launch secret token in the X-Token header. Only our own
    page can read the token (other sites cannot read our responses), and a custom header
    also forces a CORS pre-flight that we never answer.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .paths import ui_dir
from .runtime import Runtime

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
DRAIN_LIMIT = 1024 * 1024  # how much of a rejected request body we still read, so the reply is not lost
STATIC = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/favicon.svg": "favicon.svg"}
CSP = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"


class DesktopServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second process share our port; elsewhere it only skips TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: tuple[str, int], runtime: Runtime, token: str) -> None:
        super().__init__(address, _Handler)
        self.runtime, self.token = runtime, token
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: DesktopServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 – http.server naming
        log.debug("%s %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # -- helpers ---------------------------------------------------------------------
    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if status >= 400:  # an unread request body would corrupt the next request on this connection
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self) -> bytes | None:
        """Always consume the request body first. Closing a connection with unread data makes some systems
        (macOS) reset it, and the client then never sees our 401/403/400 reply. None = unusable body."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0:
            return None
        raw = self.rfile.read(min(length, DRAIN_LIMIT)) if length else b""
        return raw if length <= MAX_BODY else None

    def _parse_json(self, raw: bytes | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        if not raw:
            return {}
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return None
        try:
            body = json.loads(raw)
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    # -- routing ------------------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        raw = self._read_body()
        if self.headers.get("Host") not in self.server.allowed_hosts:
            return self._json(403, {"error": "forbidden"})
        parts = urlsplit(self.path)
        if not parts.path.startswith("/api/"):
            return self._static(method, parts.path)

        public = parts.path == "/api/ping"
        if not public and not hmac.compare_digest(self.headers.get("X-Token", ""), self.server.token):
            return self._json(401, {"error": "unauthorized"})
        body = self._parse_json(raw)
        if body is None:
            return self._json(400, {"error": "bad request"})
        try:
            status, data = self.server.runtime.request(method, parts.path, dict(parse_qsl(parts.query)), body)
        except TimeoutError:
            return self._json(504, {"error": "timeout"})
        self._json(status, data)

    def _static(self, method: str, path: str) -> None:
        name = STATIC.get(path)
        if method != "GET" or name is None:
            return self._json(404 if method == "GET" else 405, {"error": "not found"})
        payload = (ui_dir() / name).read_bytes()
        if name == "index.html":
            payload = payload.replace(b"__TOKEN__", self.server.token.encode())
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send(200, f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type, payload)
