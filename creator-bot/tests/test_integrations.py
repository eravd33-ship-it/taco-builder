"""YouTube and Google Drive modules, with the network replaced by httpx.MockTransport."""

import json
from datetime import timedelta

import httpx
import pytest

from creator_bot.modules.drive import DriveModule
from creator_bot.modules.motivation import MotivationModule
from creator_bot.modules.videos import VideosModule
from creator_bot.modules.youtube_stats import YouTubeStatsModule

from .conftest import TODAY


def youtube_transport(snapshots, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        views, subscribers = snapshots.pop(0)
        stats = {"viewCount": str(views), "subscriberCount": str(subscribers), "videoCount": "12"}
        return httpx.Response(200, json={"items": [{"snippet": {"title": "הערוץ שלי"}, "statistics": stats}]})

    return httpx.MockTransport(handler)


async def test_youtube_snapshots_and_milestones(make_core, clock, monkeypatch):
    seen = []
    transport = youtube_transport([(900, 95), (1200, 101), (1210, 101)], seen)
    monkeypatch.setattr(YouTubeStatsModule, "client_transport", transport)
    core = make_core([MotivationModule, YouTubeStatsModule], youtube_api_key="KEY", youtube_channel="@mychannel")
    stats = core.get("youtube_stats")

    await stats.refresh()
    assert seen[0]["forHandle"] == "@mychannel" and seen[0]["key"] == "KEY"
    assert core.bot.sent == []  # no milestone on the very first snapshot

    clock.advance(1)
    await stats.refresh()
    assert len(core.bot.sent) == 2  # crossed 1,000 views and 100 subscribers
    assert any("1,000" in text and "צפיות" in text for text in core.bot.sent)

    await stats.refresh()  # same day again: milestones must not repeat
    assert len(core.bot.sent) == 2

    day = TODAY + timedelta(days=1)
    assert stats.service.views_between(TODAY, day) == 310
    section = await stats.morning_section(day)
    assert "1,210 צפיות (+310)" in section.text and "(+6)" in section.text


async def test_views_goal_progress(make_core, monkeypatch):
    monkeypatch.setattr(YouTubeStatsModule, "client_transport", youtube_transport([], []))
    core = make_core([MotivationModule, YouTubeStatsModule], youtube_api_key="K", youtube_channel="UC123")
    stats = core.get("youtube_stats").service
    from creator_bot.modules.youtube_stats import ChannelStats

    stats.save(TODAY.replace(day=1), ChannelStats("c", 1000, 10, 1))
    stats.save(TODAY, ChannelStats("c", 3500, 12, 2))
    core.db.set("motivation.views_per_month", 10_000)
    section = await core.get("motivation").morning_section(TODAY)
    assert "2,500/10,000" in section.text and "25%" in section.text


class FakeDrive:
    """In-memory imitation of the Drive REST endpoints the bot uses."""

    def __init__(self):
        self.token_calls = 0
        self.folders = {}
        self.files = {}
        self.patches = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "oauth2.googleapis.com":
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer token"
        if request.method == "POST" and path == "/drive/v3/files":
            body = json.loads(request.content)
            parent = (body.get("parents") or [None])[0]
            if parent and parent not in self.folders:
                return httpx.Response(404, json={"error": "parent not found"})
            new_id = f"id{len(self.folders) + len(self.files) + 1}"
            if body.get("mimeType") == "application/vnd.google-apps.folder":
                self.folders[new_id] = (body["name"], parent)
                return httpx.Response(200, json={"id": new_id, "webViewLink": f"https://drive.test/{new_id}"})
            self.files[new_id] = body["name"]
            return httpx.Response(200, json={"id": new_id})
        if request.method == "PATCH" and path.startswith("/upload/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[-1]
            if file_id not in self.files:
                return httpx.Response(404, json={"error": "file not found"})
            self.patches.append((file_id, request.content))
            return httpx.Response(200, json={"id": file_id})
        return httpx.Response(400)


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(DriveModule, "client_transport", httpx.MockTransport(fake))
    return fake


GOOGLE = {"google_client_id": "id", "google_client_secret": "secret", "google_refresh_token": "refresh"}


async def test_drive_folder_per_video(make_core, drive):
    core = make_core([VideosModule, DriveModule], **GOOGLE)
    video = core.get("videos").service.create("טיול בצפון", TODAY + timedelta(days=7), TODAY)
    await core.events.emit("video.created", video=video, idea_id=None)

    names = [name for name, _ in drive.folders.values()]
    assert names[:3] == ["🎬 YouTube – Creator Bot", "סרטונים", "גיבויים"]
    assert "2026-10-20 · טיול בצפון" in names
    assert {"חומרי גלם", "עריכה", "תמונה ממוזערת"} <= set(names)
    assert "https://drive.test/" in core.bot.sent[-1]
    assert drive.token_calls == 1  # the access token is reused

    # the user deleted the bot's folder in Drive: it is recreated instead of failing
    drive.folders.clear()
    other = core.get("videos").service.create("עוד סרטון", TODAY + timedelta(days=9), TODAY)
    await core.events.emit("video.created", video=other, idea_id=None)
    assert "2026-10-22 · עוד סרטון" in [name for name, _ in drive.folders.values()]


async def test_drive_backup_rotates_and_recovers(make_core, drive):
    core = make_core([DriveModule], **GOOGLE)
    drive_module = core.get("drive")

    await drive_module.backup(TODAY)
    await drive_module.backup(TODAY)  # same weekday -> same file is overwritten
    assert list(drive.files.values()) == ["creator-bot-backup-2.db"]
    assert len(drive.patches) == 2 and drive.patches[0][1].startswith(b"SQLite format 3")

    await drive_module.backup(TODAY + timedelta(days=1))
    assert len(drive.files) == 2

    drive.files.clear()  # backup files deleted by the user
    await drive_module.backup(TODAY)
    assert list(drive.files.values()) == ["creator-bot-backup-2.db"]
