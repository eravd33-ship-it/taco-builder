"""Morning / evening reminders and the background jobs of the desktop app."""

from datetime import timedelta

import httpx
import pytest

from creator_bot.modules.drive import DriveModule
from creator_bot.modules.youtube_stats import YouTubeStatsModule

from .conftest import TODAY
from .test_integrations import GOOGLE, FakeDrive, youtube_transport


def at(clock, hour, minute=0, days=0):
    clock.now = clock.now.replace(hour=hour, minute=minute) + timedelta(days=days)


def texts(desk):
    return [item["text"] for item in reversed(desk.app.feed.to_json()["items"])]


async def test_morning_digest_is_sent_once_a_day(make_desk, clock):
    desk = make_desk()
    desk.app.videos.service.create("ולוג", TODAY, TODAY)

    at(clock, 8, 59)
    await desk.app.tick()
    assert texts(desk) == []

    at(clock, 9, 0)
    await desk.app.tick()
    await desk.app.tick()
    assert len(texts(desk)) == 1 and "בוקר טוב" in texts(desk)[0] and "לצלם: ולוג" in texts(desk)[0]
    assert desk.shown and "בוקר טוב" in desk.shown[0][0]  # also a pop-up, since no window is open

    at(clock, 9, 0, days=1)  # the next day it comes again
    await desk.app.tick()
    assert len(texts(desk)) == 2


async def test_late_launch_catches_up(make_desk, clock):
    desk = make_desk()
    at(clock, 14, 30)  # the app was started in the afternoon
    await desk.app.tick()
    assert len(texts(desk)) == 1 and "בוקר טוב" in texts(desk)[0]


async def test_evening_checkin_is_silent_when_nothing_is_open(make_desk, clock):
    desk = make_desk()
    at(clock, 20, 30)
    await desk.app.tick()
    assert texts(desk) == []


async def test_evening_checkin_lists_what_is_still_open(make_desk, clock):
    desk = make_desk()
    desk.app.videos.service.create("עוד סרטון", TODAY, TODAY)
    at(clock, 20, 30)
    await desk.app.tick()
    [message] = texts(desk)
    assert "צ'ק-אין" in message and "עדיין פתוח" in message and "עוד סרטון" in message and "משימה אחת" in message


async def test_opening_late_at_night_skips_the_good_morning(make_desk, clock):
    desk = make_desk()
    desk.app.videos.service.create("סרטון", TODAY, TODAY)
    at(clock, 22, 0)
    await desk.app.tick()
    assert all("בוקר טוב" not in text for text in texts(desk))
    assert any("צ'ק-אין" in text for text in texts(desk))


async def test_reminder_times_are_configurable(make_desk, clock):
    desk = make_desk()
    await desk.ok("PUT", "/api/settings", {"morning": "07:00"})
    at(clock, 7, 5)
    await desk.app.tick()
    assert len(texts(desk)) == 1


async def test_youtube_refresh_runs_in_the_background_and_retries_after_failure(make_desk, clock, monkeypatch):
    calls = []
    transport = youtube_transport([(1000, 100)], calls)
    monkeypatch.setattr(YouTubeStatsModule, "client_transport", transport)
    desk = make_desk(youtube_api_key="K", youtube_channel="UC1")

    await desk.app.tick()
    await desk.settle()
    assert len(calls) == 1
    channel = (await desk.ok("GET", "/api/today"))["channel"]
    assert channel["views"] == 1000 and channel["subscribers"] == 100 and channel["title"] == "הערוץ שלי"

    await desk.app.tick()
    await desk.settle()
    assert len(calls) == 1  # today's snapshot exists: no more requests

    # a failure is retried only after the back-off
    def broken(request):
        calls.append("broken")
        return httpx.Response(500)

    monkeypatch.setattr(YouTubeStatsModule, "client_transport", httpx.MockTransport(broken))
    desk2 = make_desk(youtube_api_key="K", youtube_channel="UC1")
    await desk2.app.tick()
    await desk2.settle()
    await desk2.app.tick()
    await desk2.settle()
    assert calls.count("broken") == 1
    at(clock, 9, 31)
    await desk2.app.tick()
    await desk2.settle()
    assert calls.count("broken") == 2


async def test_drive_backup_once_a_day_and_via_button(make_desk, clock, monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(DriveModule, "client_transport", httpx.MockTransport(fake))
    desk = make_desk(**GOOGLE)

    await desk.app.tick()
    await desk.settle()
    assert list(fake.files.values()) == ["creator-bot-backup-2.db"]
    await desk.app.tick()
    await desk.settle()
    assert len(fake.patches) == 1  # not again the same day

    result = await desk.ok("POST", "/api/drive/backup")
    assert result["url"].startswith("https://drive.test/") and len(fake.patches) == 2

    # a Drive folder is created for each new video and linked from the video card
    video = (await desk.call("POST", "/api/videos", {"title": "טיול", "date": (TODAY).isoformat()}))[1]
    await desk.settle()
    card = (await desk.ok("GET", "/api/videos"))["active"][0]
    assert card["id"] == video["id"] and card["drive_url"].startswith("https://drive.test/")
    assert any("תיקייה" in text for text in texts(desk))


async def test_stats_refresh_button_reports_failures(make_desk, monkeypatch):
    monkeypatch.setattr(YouTubeStatsModule, "client_transport", httpx.MockTransport(lambda r: httpx.Response(403)))
    desk = make_desk(youtube_api_key="K", youtube_channel="UC1")
    status, data = await desk.call("POST", "/api/stats/refresh")
    assert status == 502 and "מפתח" in data["error"]


@pytest.mark.parametrize("path", ["/api/drive/backup"])
async def test_drive_button_reports_failures(make_desk, monkeypatch, path):
    monkeypatch.setattr(DriveModule, "client_transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    desk = make_desk(**GOOGLE)
    assert (await desk.call("POST", path))[0] == 502
