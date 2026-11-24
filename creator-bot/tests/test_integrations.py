"""The YouTube stats module, with the network replaced by httpx.MockTransport."""

from datetime import timedelta

import httpx

from creator_bot.modules.motivation import MotivationModule
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
