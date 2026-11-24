"""The YouTube channel in the desktop app: found by its name + an API key, with no Google sign-in."""

import httpx
import pytest

from creator_bot.desktop.app import DesktopApp
from creator_bot.modules.youtube_stats import YouTubeStatsModule, normalize_channel
from creator_bot.storage import Database

from .test_integrations import youtube_transport

KEY = "AIza" + "x" * 35
CHANNEL_ID = "UC" + "a" * 22


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("@MyChannel", "@MyChannel"),
        ("MyChannel", "@MyChannel"),
        ("  @my.channel_1  ", "@my.channel_1"),
        ("https://www.youtube.com/@MyChannel", "@MyChannel"),
        ("youtube.com/@MyChannel/videos", "@MyChannel"),
        ("https://m.youtube.com/@%D7%99%D7%95%D7%98%D7%99%D7%95%D7%91", "@יוטיוב"),
        (CHANNEL_ID, CHANNEL_ID),
        (f"https://www.youtube.com/channel/{CHANNEL_ID}", CHANNEL_ID),
        ("", None),
        (None, None),
        ("ab", None),
        ("שם עם רווח", None),
        ("https://evil.example/@MyChannel", None),
        ("https://www.youtube.com/watch?v=abc", None),
    ],
)
def test_normalize_channel(typed, expected):
    assert normalize_channel(typed) == expected


def serve(monkeypatch, seen=None):
    """YouTube answers with 1,000 views and 100 subscribers, and `seen` collects the request parameters."""
    transport = youtube_transport([(1000, 100)] * 5, [] if seen is None else seen)
    monkeypatch.setattr(YouTubeStatsModule, "client_transport", transport)


async def test_not_set_up_by_default(make_desk):
    desk = make_desk()
    assert await desk.ok("GET", "/api/youtube") == {
        "source": "app",
        "channel": "",
        "has_key": False,
        "connected": False,
        "title": None,
    }


async def test_saving_a_channel_name_and_key_shows_the_numbers(make_desk, monkeypatch):
    seen = []
    serve(monkeypatch, seen)
    desk = make_desk()

    state = await desk.ok("PUT", "/api/youtube", {"channel": "https://www.youtube.com/@MyChannel", "api_key": KEY})
    assert state == {"source": "app", "channel": "@MyChannel", "has_key": True, "connected": True, "title": "הערוץ שלי"}
    assert seen[0]["forHandle"] == "@MyChannel" and seen[0]["key"] == KEY and "mine" not in seen[0]
    assert (await desk.ok("GET", "/api/settings"))["youtube"] is True
    channel = (await desk.ok("GET", "/api/today"))["channel"]
    assert channel["views"] == 1000 and channel["subscribers"] == 100
    assert KEY not in str(await desk.ok("GET", "/api/youtube"))  # the key is never sent back to the page

    # the channel can be changed without typing the key again
    await desk.ok("PUT", "/api/youtube", {"channel": CHANNEL_ID})
    assert seen[-1]["id"] == CHANNEL_ID and seen[-1]["key"] == KEY

    # ...and it is still there after the app is closed and opened again
    again = DesktopApp(desk.app._env_settings, desk.app.db, desk.app.core.now)
    assert again.stats is not None and again.settings.youtube_channel == CHANNEL_ID


async def test_bad_input_is_explained_and_nothing_is_saved(make_desk, monkeypatch):
    serve(monkeypatch)
    desk = make_desk()
    assert (await desk.call("PUT", "/api/youtube", {"channel": "שם עם רווח", "api_key": KEY}))[0] == 400
    assert (await desk.call("PUT", "/api/youtube", {"channel": "@MyChannel"}))[0] == 400  # no key yet
    assert (await desk.call("PUT", "/api/youtube", {"channel": "@MyChannel", "api_key": "short"}))[0] == 400

    monkeypatch.setattr(YouTubeStatsModule, "client_transport", httpx.MockTransport(lambda r: httpx.Response(400)))
    status, data = await desk.call("PUT", "/api/youtube", {"channel": "@MyChannel", "api_key": KEY})
    assert status == 400 and "מפתח" in data["error"]

    monkeypatch.setattr(
        YouTubeStatsModule, "client_transport", httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []}))
    )
    status, data = await desk.call("PUT", "/api/youtube", {"channel": "@Nobody", "api_key": KEY})
    assert status == 404 and "@Nobody" in data["error"]

    monkeypatch.setattr(YouTubeStatsModule, "client_transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    assert (await desk.call("PUT", "/api/youtube", {"channel": "@MyChannel", "api_key": KEY}))[0] == 502

    assert (await desk.ok("GET", "/api/youtube"))["connected"] is False
    assert desk.app.db.get("youtube.api_key") is None and desk.app.db.get("youtube.channel") is None


async def test_removing_the_channel(make_desk, monkeypatch):
    serve(monkeypatch)
    desk = make_desk()
    await desk.ok("PUT", "/api/youtube", {"channel": "@MyChannel", "api_key": KEY})
    state = await desk.ok("DELETE", "/api/youtube")
    assert not state["connected"] and not state["has_key"] and state["channel"] == ""
    assert (await desk.ok("GET", "/api/today"))["channel"] is None


async def test_a_channel_from_the_env_file_cannot_be_changed_in_the_app(make_desk):
    desk = make_desk(youtube_api_key=KEY, youtube_channel="@FromEnv")
    state = await desk.ok("GET", "/api/youtube")
    assert state["source"] == "env" and state["connected"] and state["channel"] == "@FromEnv"
    assert (await desk.call("PUT", "/api/youtube", {"channel": "@Other", "api_key": KEY}))[0] == 409
    assert (await desk.call("DELETE", "/api/youtube"))[0] == 409


def test_a_google_sign_in_saved_by_an_older_version_is_wiped(clock):
    db = Database()
    for key in ("client_id", "client_secret", "refresh_token", "scopes"):
        db.set(f"google.{key}", f"old-{key}")
    from creator_bot.config import Settings

    app = DesktopApp(Settings(telegram_token="", owner_chat_id=1), db, clock)
    assert all(db.get(f"google.{key}") is None for key in ("client_id", "client_secret", "refresh_token", "scopes"))
    assert app.stats is None
