"""One-time helper: get a Google refresh token for the Drive module.

    python -m creator_bot.tools.google_auth

Reads GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from .env (or asks for them),
opens the browser for consent and prints the GOOGLE_REFRESH_TOKEN line to
paste into .env. Uses only the standard library.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from ..config import load_dotenv

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    client_id = os.environ.get("GOOGLE_CLIENT_ID") or input("GOOGLE_CLIENT_ID: ").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or input("GOOGLE_CLIENT_SECRET: ").strip()

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 – http.server naming
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2 dir='rtl'>✅ אפשר לסגור את החלון ולחזור לטרמינל.</h2>".encode())

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Callback)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    print("\nפותח את הדפדפן לאישור גישה לגוגל דרייב. אם הוא לא נפתח, היכנס לקישור:\n")
    print(url + "\n")
    webbrowser.open(url)
    while not result:
        server.handle_request()
    server.server_close()

    if not result.get("code"):
        raise SystemExit(f"האישור נכשל: {result.get('error') or 'unknown error'}")

    body = urllib.parse.urlencode(
        {
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as response:
        tokens = json.loads(response.read())
    if "refresh_token" not in tokens:
        raise SystemExit(f"לא התקבל refresh token: {tokens}")
    print("✅ הצלחה! הוסף את השורה הזו לקובץ .env:\n")
    print(f"GOOGLE_REFRESH_TOKEN={tokens['refresh_token']}\n")


if __name__ == "__main__":
    main()
