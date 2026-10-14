"""Signing in with Google from inside the desktop app (YouTube stats + Drive), with Google faked."""

import base64
import hashlib
import urllib.parse
from datetime import timedelta

import httpx
import pytest

from creator_bot.core.events import EventBus
from creator_bot.google_auth import DRIVE_SCOPE, SCOPES, YOUTUBE_SCOPE, GoogleToken, build_auth_url, make_pkce
from creator_bot.modules.drive import DriveModule
from creator_bot.modules.youtube_stats import YouTubeStatsModule

from .conftest import TODAY
from .test_integrations import GOOGLE, FakeDrive

CLIENT_ID = "1234-abcd.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-secret-value"
CLIENT = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}


class World:
    """Google, as far as the app can tell: the token endpoint, the YouTube API and Drive."""

    def __init__(self) -> None:
        self.granted = list(SCOPES)
        self.fail_exchange = False
        self.token_requests: list[dict] = []
        self.youtube_requests: list[tuple[dict, str | None]] = []
        self.drive = FakeDrive()
        self.transport = httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                if self.fail_exchange:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                body = {
                    "access_token": "token",
                    "refresh_token": "RT-1",
                    "expires_in": 3600,
                    "scope": " ".join(self.granted),
                }
                return httpx.Response(200, json=body)
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.url.path == "/youtube/v3/channels":
            self.youtube_requests.append((dict(request.url.params), request.headers.get("authorization")))
            stats = {"viewCount": "1000", "subscriberCount": "100", "videoCount": "12"}
            return httpx.Response(200, json={"items": [{"snippet": {"title": "הערוץ שלי"}, "statistics": stats}]})
        return self.drive(request)


@pytest.fixture
def world(monkeypatch):
    world = World()
    monkeypatch.setattr(YouTubeStatsModule, "client_transport", world.transport)
    monkeypatch.setattr(DriveModule, "client_transport", world.transport)
    return world


@pytest.fixture
def desk(make_desk, world):
    desk = make_desk()
    desk.app.port = 8765
    desk.app.google_transport = world.transport
    return desk


def state_of(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]


async def sign_in(desk, **kwargs):
    url = (await desk.ok("POST", "/api/google/connect", CLIENT))["url"]
    return await desk.call("GET", "/oauth/callback", query={"code": "one-time-code", "state": state_of(url), **kwargs})


async def test_signing_in_connects_youtube_and_drive(desk, world):
    status = await desk.ok("GET", "/api/google")
    assert (status["connected"], status["has_client"], status["youtube"], status["drive"]) == (
        False,
        False,
        False,
        False,
    )

    url = (await desk.ok("POST", "/api/google/connect", CLIENT))["url"]
    assert desk.opened == [url]  # opened in the system browser
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["client_id"] == [CLIENT_ID] and query["redirect_uri"] == ["http://127.0.0.1:8765/oauth/callback"]
    assert set(query["scope"][0].split()) == {DRIVE_SCOPE, YOUTUBE_SCOPE}
    assert query["access_type"] == ["offline"] and query["code_challenge_method"] == ["S256"]
    assert (await desk.ok("GET", "/api/google"))["waiting"]

    status, data = await desk.call("GET", "/oauth/callback", query={"code": "one-time-code", "state": state_of(url)})
    assert status == 200 and "החיבור הושלם" in data["message"]
    await desk.settle()

    exchange = world.token_requests[0]
    assert (
        exchange["code"] == "one-time-code" and exchange["client_secret"] == CLIENT_SECRET and exchange["code_verifier"]
    )
    status = await desk.ok("GET", "/api/google")
    assert (status["connected"], status["youtube"], status["drive"], status["waiting"]) == (True, True, True, False)
    assert "client_secret" not in status and CLIENT_SECRET not in str(status)
    assert list(desk.app.core.modules) == ["motivation", "youtube_stats", "videos", "routines", "ideas", "drive"]

    # the channel was fetched right away, as the signed-in user's own ("mine"), with the access token
    params, authorization = world.youtube_requests[0]
    assert params["mine"] == "true" and "key" not in params and authorization == "Bearer token"
    channel = (await desk.ok("GET", "/api/today"))["channel"]
    assert (channel["title"], channel["views"], channel["subscribers"]) == ("הערוץ שלי", 1000, 100)
    assert any("חובר לחשבון גוגל" in item["text"] for item in desk.app.feed.to_json()["items"])

    # the sign-in is single use
    assert (await desk.call("GET", "/oauth/callback", query={"code": "again", "state": state_of(url)}))[0] == 400


async def test_modules_loaded_later_do_not_double_up_the_existing_ones(desk, world):
    await sign_in(desk)
    await desk.settle()
    video = (await desk.call("POST", "/api/videos", {"title": "טיול בצפון", "date": TODAY.isoformat()}))[1]
    await desk.settle()
    folders = [name for name, _ in world.drive.folders.values() if name.endswith("טיול בצפון")]
    assert len(folders) == 1  # Drive listens once, not twice

    desk.app.feed.mark_all_read()
    await desk.ok("POST", f"/api/videos/{video['id']}/done", {"step": "film"})
    await desk.settle()
    praise = [item for item in desk.app.feed.to_json()["items"] if "הבא בתור" in item["text"]]
    assert len(praise) == 1  # motivation listens once, not twice


async def test_forms_are_validated(desk):
    for bad_id in ("", "abc", "1234.apps.googleusercontent.com.evil.example"):
        assert (await desk.call("POST", "/api/google/connect", {**CLIENT, "client_id": bad_id}))[0] == 400
    assert (await desk.call("POST", "/api/google/connect", {**CLIENT, "client_secret": "short"}))[0] == 400
    assert (await desk.call("POST", "/api/google/connect"))[0] == 400  # nothing saved yet
    assert desk.opened == []


async def test_callbacks_we_did_not_ask_for_are_rejected(desk, world):
    assert (await desk.call("GET", "/oauth/callback", query={"code": "x", "state": "guess"}))[
        0
    ] == 400  # nothing pending

    await desk.ok("POST", "/api/google/connect", CLIENT)
    status, data = await desk.call("GET", "/oauth/callback", query={"code": "x", "state": "wrong"})
    assert status == 400 and "פגה או לא תקינה" in data["error"]
    assert world.token_requests == []  # the code was never even sent to Google
    assert not (await desk.ok("GET", "/api/google"))["waiting"]  # a wrong guess burns the pending sign-in


async def test_cancelled_expired_and_failed_sign_ins(desk, world, clock):
    url = (await desk.ok("POST", "/api/google/connect", CLIENT))["url"]
    status, data = await desk.call("GET", "/oauth/callback", query={"error": "access_denied", "state": state_of(url)})
    assert status == 400 and "בוטל" in data["error"]

    url = (await desk.ok("POST", "/api/google/connect", CLIENT))["url"]
    clock.now += timedelta(minutes=11)
    assert (await desk.call("GET", "/oauth/callback", query={"code": "x", "state": state_of(url)}))[0] == 400

    world.fail_exchange = True
    status, data = await sign_in(desk)
    assert status == 502 and "Desktop app" in data["error"]
    assert not (await desk.ok("GET", "/api/google"))["connected"]


async def test_only_the_granted_parts_are_enabled(desk, world):
    world.granted = [DRIVE_SCOPE]  # the user un-ticked YouTube on Google's screen
    assert (await sign_in(desk))[0] == 200
    await desk.settle()
    status = await desk.ok("GET", "/api/google")
    assert (status["youtube"], status["drive"]) == (False, True)
    assert any("לא ניתנה הרשאה ל: יוטיוב" in item["text"] for item in desk.app.feed.to_json()["items"])
    assert world.youtube_requests == []


async def test_disconnecting_removes_everything(desk, world):
    await sign_in(desk)
    await desk.settle()
    status = await desk.ok("DELETE", "/api/google")
    assert (status["connected"], status["has_client"], status["youtube"], status["drive"]) == (
        False,
        False,
        False,
        False,
    )
    assert desk.app.db.get("google.refresh_token") is None and desk.app.db.get("google.client_secret") is None

    await desk.call("POST", "/api/videos", {"title": "אחרי הניתוק", "date": TODAY.isoformat()})
    await desk.settle()
    assert not [name for name, _ in world.drive.folders.values() if name.endswith("אחרי הניתוק")]  # unsubscribed


async def test_a_new_client_invalidates_the_old_sign_in(desk):
    await sign_in(desk)
    await desk.ok("POST", "/api/google/connect", {**CLIENT, "client_id": "9999-other.apps.googleusercontent.com"})
    assert not (await desk.ok("GET", "/api/google"))["connected"]


async def test_google_configured_in_env_wins(make_desk, world):
    desk = make_desk(**GOOGLE)
    status = await desk.ok("GET", "/api/google")
    assert status["source"] == "env" and status["connected"] and status["drive"] and not status["youtube"]
    assert (await desk.call("POST", "/api/google/connect", CLIENT))[0] == 409
    assert (await desk.call("DELETE", "/api/google"))[0] == 409


async def test_api_key_mode_is_unchanged(make_desk, world):
    desk = make_desk(youtube_api_key="KEY", youtube_channel="@mine")
    await desk.ok("POST", "/api/stats/refresh")
    params, authorization = world.youtube_requests[0]
    assert params["key"] == "KEY" and params["forHandle"] == "@mine" and "mine" not in params and authorization is None


def test_pkce_auth_url_and_token_cache(world):
    verifier, challenge = make_pkce()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert make_pkce()[0] != verifier
    url = build_auth_url("cid", "http://127.0.0.1:1/oauth/callback", "st", challenge)
    assert url.startswith("https://accounts.google.com/") and "code_challenge=" + challenge in url


async def test_access_tokens_are_reused_until_they_expire(world):
    token = GoogleToken("cid", "secret", "refresh", world.transport)
    assert await token.get() == await token.get() == "token"
    assert len(world.token_requests) == 1


async def test_modules_can_be_unloaded_without_leaving_listeners(make_core):
    core = make_core([DriveModule], **GOOGLE)
    assert core.get("drive")
    core.unload("drive")
    assert core.get("drive") is None and core.events._handlers["video.created"] == []
    core.unload("drive")  # twice is fine

    bus, calls = EventBus(), []

    async def handler(**kw):
        calls.append(kw)

    bus.on("x", handler)
    bus.off("x", handler)
    bus.off("x", handler)
    await bus.emit("x")
    assert calls == []
