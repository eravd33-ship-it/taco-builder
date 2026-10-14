"""Start-up check for a built Creator Desk.

Launch the app first (`<app> --no-window --port PORT`), then run:  python packaging/smoke_test.py PORT
It waits for the app, checks the page, the files and the API, then asks the app to quit.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request


def main(port: int) -> int:
    base = f"http://127.0.0.1:{port}"

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
    for asset in ("/app.js", "/app.css", "/favicon.svg"):
        assert urllib.request.urlopen(base + asset, timeout=5).status == 200, f"{asset} was not packaged"

    def call(method: str, path: str) -> dict:
        request = urllib.request.Request(base + path, method=method, headers={"X-Token": token.group(1)})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    assert "quote" in call("GET", "/api/today"), "/api/today is broken"
    assert call("GET", "/api/settings")["morning"] == "09:00"
    call("POST", "/api/quit")
    print("Smoke test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
