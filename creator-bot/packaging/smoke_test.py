"""Start-up check for a built Creator Desk.

Launch the app first (`<app> --no-window --port PORT`), then run:  python packaging/smoke_test.py PORT [FAKE_PORT]
It waits for the app, checks the page, the files and the API, then asks the app to quit.

With FAKE_PORT it also checks the AI assistant end to end inside the built app: it starts a fake Anthropic API on that
port, and the app must have been launched with ANTHROPIC_BASE_URL=http://127.0.0.1:FAKE_PORT and
ANTHROPIC_API_KEY=sk-ant-smoke-test-key-000000 (and GEMINI_BASE_URL=http://127.0.0.1:FAKE_PORT/v1beta for the free
Gemini assistant). The real SDK / HTTP client inside the app then talks to the fake.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_anthropic import GEMINI_SMOKE_KEY, SMOKE_REPLY, FakeAnthropic  # noqa: E402


def main(port: int, fake_port: int | None = None) -> int:
    base = f"http://127.0.0.1:{port}"
    fake = FakeAnthropic(fake_port) if fake_port else None

    deadline = time.time() + 90
    while True:
        try:
            with urllib.request.urlopen(f"{base}/api/ping", timeout=2) as response:
                if json.load(response).get("app") == "creator-desk":
                    break
        except OSError:
            pass
        if time.time() > deadline:
            print("The app did not start within 90 seconds")
            return 1
        time.sleep(1)

    page = urllib.request.urlopen(f"{base}/", timeout=5).read().decode()
    token = re.search(r'name="token" content="([^"]+)"', page)
    assert token and "__TOKEN__" not in page, "the page has no session token"
    for asset in ("/app.js", "/app.css", "/favicon.svg", "/icon.png"):
        assert urllib.request.urlopen(base + asset, timeout=5).status == 200, f"{asset} was not packaged"

    def call(method: str, path: str, body: dict | None = None) -> dict:
        headers = {"X-Token": token.group(1)}
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base + path, method=method, headers=headers, data=data)
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    assert "quote" in call("GET", "/api/today"), "/api/today is broken"
    assert call("GET", "/api/settings")["morning"] == "09:00"
    if fake is not None:
        status = call("GET", "/api/assistant")
        assert status["configured"] and status["source"] == "env", f"the assistant has no key: {status}"
        assert call("POST", "/api/assistant/chat", {"message": "שלום"})["reply"] == SMOKE_REPLY, "wrong assistant reply"
        sent = [r for r in fake.requests if r["path"].startswith("/v1/messages")]
        assert sent and sent[0]["body"]["model"] == "claude-opus-5-5", "the assistant did not reach the (fake) API"
        assert sent[0]["body"]["fallbacks"] == "default", "the request is missing the refusal fallback"
        print("Assistant OK")
        # The free assistant: a key typed into the app, checked and used against the fake Gemini API.
        saved = call("PUT", "/api/assistant", {"provider": "gemini", "api_key": GEMINI_SMOKE_KEY})
        assert saved["provider"] == "gemini" and saved["configured"], f"the Gemini key was not accepted: {saved}"
        assert call("POST", "/api/assistant/chat", {"message": "שלום"})["reply"] == SMOKE_REPLY, "wrong Gemini reply"
        generated = [r for r in fake.requests if r["path"].endswith(":generateContent")]
        assert generated and generated[0]["headers"]["x-goog-api-key"] == GEMINI_SMOKE_KEY, "Gemini was not reached"
        assert GEMINI_SMOKE_KEY not in generated[0]["path"], "the key must travel in a header, not in the URL"
        print("Gemini OK")
    call("POST", "/api/quit")
    print("Smoke test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None))
