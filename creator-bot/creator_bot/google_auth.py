"""Signing in with Google (OAuth for installed apps) – shared by the YouTube and Drive modules.

The user creates one "Desktop app" OAuth client in their own Google Cloud project and signs in once.
We keep only a refresh token, and ask for the narrowest scopes that do the job:
  drive.file       – the app sees only the files and folders it created itself
  youtube.readonly – read the channel's statistics
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
SCOPES = (DRIVE_SCOPE, YOUTUBE_SCOPE)
CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"


def make_pkce() -> tuple[str, str]:
    """(verifier, challenge): proves the app that starts the sign-in is the one that finishes it."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",  # we need a refresh token
            "prompt": "consent",  # ...and Google only gives one when asked every time
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTH_URL}?{query}"


async def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Trade the one-time code from the browser for tokens. Raises httpx.HTTPError on failure."""
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    response.raise_for_status()
    return response.json()


class GoogleToken:
    """Hands out short-lived access tokens, refreshing them from the long-lived refresh token."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credentials = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        self._transport = transport
        self._token: str | None = None
        self._expires = 0.0

    async def get(self) -> str:
        if self._token is None or time.monotonic() > self._expires - 60:
            async with httpx.AsyncClient(timeout=20, transport=self._transport) as client:
                response = await client.post(TOKEN_URL, data=self._credentials)
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.monotonic() + body.get("expires_in", 3600)
        return self._token
