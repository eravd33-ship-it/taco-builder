"""A tiny stand-in for api.anthropic.com, for tests. Stdlib only.

It records every request exactly as the real SDK sent it and answers like the real API would, so a test can run
the real `anthropic` client (pointed here with ANTHROPIC_BASE_URL) without a key, a network or any cost.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SMOKE_KEY = "sk-ant-smoke-test-key-000000"
SMOKE_REPLY = "תשובת בדיקה מהשרת המדומה"


class FakeAnthropic:
    def __init__(self, port: int = 0, valid_key: str = SMOKE_KEY, reply: str = SMOKE_REPLY) -> None:
        self.valid_key, self.reply = valid_key, reply
        self.requests: list[dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def _answer(self, status: int, body: dict[str, Any]) -> None:
                payload = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                fake.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {name.lower(): value for name, value in self.headers.items()},
                        "body": body,
                    }
                )
                if self.headers.get("x-api-key") != fake.valid_key:
                    return self._answer(
                        401,
                        {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
                    )
                path = self.path.split("?")[0]
                if self.command == "GET" and path.startswith("/v1/models/"):
                    model = path.rsplit("/", 1)[-1]
                    return self._answer(
                        200, {"id": model, "type": "model", "display_name": model, "created_at": "2026-01-01T00:00:00Z"}
                    )
                if self.command == "POST" and path == "/v1/messages":
                    return self._answer(
                        200,
                        {
                            "id": "msg_fake",
                            "type": "message",
                            "role": "assistant",
                            "model": (body or {}).get("model", "claude-opus-5-5"),
                            "content": [
                                {"type": "thinking", "thinking": "", "signature": "fake-signature"},
                                {"type": "text", "text": fake.reply},
                            ],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "usage": {"input_tokens": 12, "output_tokens": 7},
                        },
                    )
                self._answer(404, {"type": "error", "error": {"type": "not_found_error", "message": "not found"}})

            do_GET = do_POST = _handle  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
