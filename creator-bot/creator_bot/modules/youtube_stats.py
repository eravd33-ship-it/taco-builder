"""📈 YouTube stats (optional): daily snapshot of views/subscribers, milestones.

Needs YOUTUBE_API_KEY and YOUTUBE_CHANNEL (channel id "UC..." or "@handle").

Events emitted:
  channel.milestone(metric, value) – e.g. ("subscribers", 1000)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
from telegram import Update
from telegram.ext import JobQueue

from ..config import Settings
from ..core import ui
from ..core.module import Command, Ctx, DigestSection, MenuButton, Module
from ..storage import Database

log = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3/channels"
SUBSCRIBER_MILESTONES = (
    10,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    10_000,
    25_000,
    50_000,
    100_000,
    250_000,
    500_000,
    1_000_000,
)
VIEW_MILESTONES = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 5_000_000, 10_000_000)

SCHEMA = [
    """
    CREATE TABLE yt_snapshots (
        day TEXT PRIMARY KEY,
        views INTEGER NOT NULL,
        subscribers INTEGER,
        videos INTEGER NOT NULL
    );
    """,
]


@dataclass
class ChannelStats:
    title: str
    views: int
    subscribers: int | None  # None when the channel hides its subscriber count
    videos: int


class YouTubeClient:
    def __init__(self, api_key: str, channel: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.channel = channel.strip()
        self.transport = transport

    async def fetch(self) -> ChannelStats:
        params = {"part": "statistics,snippet", "key": self.api_key}
        if self.channel.startswith("@"):
            params["forHandle"] = self.channel
        else:
            params["id"] = self.channel
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            response = await client.get(API_URL, params=params)
        response.raise_for_status()
        items = response.json().get("items") or []
        if not items:
            raise LookupError(f"YouTube channel not found: {self.channel}")
        stats = items[0]["statistics"]
        subscribers = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0))
        return ChannelStats(
            title=items[0]["snippet"]["title"],
            views=int(stats.get("viewCount", 0)),
            subscribers=subscribers,
            videos=int(stats.get("videoCount", 0)),
        )


def crossed(previous: int, current: int, milestones: tuple[int, ...]) -> list[int]:
    return [m for m in milestones if previous < m <= current]


class StatsService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, day: date, stats: ChannelStats) -> None:
        self.db.execute(
            "INSERT INTO yt_snapshots (day, views, subscribers, videos) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET views = excluded.views, subscribers = excluded.subscribers, "
            "videos = excluded.videos",
            (day.isoformat(), stats.views, stats.subscribers, stats.videos),
        )

    def on_or_before(self, day: date) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM yt_snapshots WHERE day <= ? ORDER BY day DESC LIMIT 1", (day.isoformat(),)
        )
        return dict(row) if row else None

    def views_between(self, start: date, end: date) -> int | None:
        """Views gained from the start of `start` until `end` (needs 2+ snapshots)."""
        latest = self.on_or_before(end)
        baseline = self.on_or_before(start - timedelta(days=1))
        if baseline is None:  # tracking started mid-period: count from the first snapshot
            row = self.db.query_one(
                "SELECT * FROM yt_snapshots WHERE day >= ? ORDER BY day LIMIT 1", (start.isoformat(),)
            )
            baseline = dict(row) if row else None
        if latest is None or baseline is None or latest["day"] == baseline["day"]:
            return None
        return latest["views"] - baseline["views"]


class YouTubeStatsModule(Module):
    name = "youtube_stats"
    client_transport: httpx.AsyncBaseTransport | None = None  # tests swap the network

    @classmethod
    def is_enabled(cls, settings: Settings) -> bool:
        return bool(settings.youtube_api_key and settings.youtube_channel)

    def setup(self) -> None:
        self.core.db.migrate(self.name, SCHEMA)
        self.service = StatsService(self.core.db)
        settings = self.core.settings
        self.client = YouTubeClient(settings.youtube_api_key, settings.youtube_channel, self.client_transport)
        self.channel_title: str | None = None

    def menu_buttons(self) -> list[MenuButton]:
        return [MenuButton("📈 נתוני ערוץ", self.show_stats, order=50)]

    def commands(self) -> list[Command]:
        return [Command("stats", "נתוני הערוץ ביוטיוב", self.show_stats)]

    def schedule(self, job_queue: JobQueue) -> None:
        # Refresh shortly before the morning digest, and once at startup.
        morning = self.core.settings.morning_time
        before = (datetime.combine(date.today(), morning) - timedelta(minutes=15)).time()
        job_queue.run_daily(self._refresh_job, before, name="youtube_stats")
        job_queue.run_once(self._refresh_job, 10, name="youtube_stats_startup")

    async def _refresh_job(self, context: Ctx) -> None:
        try:
            await self.refresh()
        except (httpx.HTTPError, LookupError, KeyError, ValueError):
            log.exception("Could not refresh YouTube stats")

    async def refresh(self) -> ChannelStats:
        stats = await self.client.fetch()
        today = self.core.today()
        previous = self.service.on_or_before(today)  # most recent values, so a milestone fires once
        self.service.save(today, stats)
        self.channel_title = stats.title
        if previous is not None:
            for value in crossed(previous["views"], stats.views, VIEW_MILESTONES):
                await self.core.events.emit("channel.milestone", metric="views", value=value)
            if stats.subscribers is not None and previous["subscribers"] is not None:
                for value in crossed(previous["subscribers"], stats.subscribers, SUBSCRIBER_MILESTONES):
                    await self.core.events.emit("channel.milestone", metric="subscribers", value=value)
        return stats

    async def show_stats(self, update: Update, context: Ctx) -> None:
        try:
            stats = await self.refresh()
        except (httpx.HTTPError, LookupError, KeyError, ValueError):
            log.exception("Could not fetch YouTube stats")
            await ui.reply(
                update, "😕 לא הצלחתי למשוך נתונים מיוטיוב. כדאי לבדוק את YOUTUBE_API_KEY ו-YOUTUBE_CHANNEL."
            )
            return
        today = self.core.today()
        yesterday = self.service.on_or_before(today - timedelta(days=1))
        lines = [f"📈 <b>{ui.esc(stats.title)}</b>", ""]
        views_delta = f" (+{stats.views - yesterday['views']:,} מאתמול)" if yesterday else ""
        lines.append(f"👀 צפיות: <b>{stats.views:,}</b>{views_delta}")
        if stats.subscribers is not None:
            lines.append(f"👥 מנויים: <b>{stats.subscribers:,}</b>")
        lines.append(f"🎬 סרטונים: <b>{stats.videos:,}</b>")
        week = self.service.views_between(today - timedelta(days=6), today)
        if week is not None:
            lines += ["", f"📅 7 ימים אחרונים: +{week:,} צפיות"]
        await ui.reply(update, "\n".join(lines))

    async def morning_section(self, day: date) -> DigestSection | None:
        latest = self.service.on_or_before(day)
        if latest is None:
            return None
        previous = self.service.on_or_before(date.fromisoformat(latest["day"]) - timedelta(days=1))
        views = f"{latest['views']:,} צפיות"
        if previous:
            views += f" (+{latest['views'] - previous['views']:,})"
        parts = [views]
        if latest["subscribers"] is not None:
            subs = f"{latest['subscribers']:,} מנויים"
            if previous and previous["subscribers"] is not None:
                subs += f" ({latest['subscribers'] - previous['subscribers']:+,})"
            parts.append(subs)
        return DigestSection("📈 <b>הערוץ:</b> " + " · ".join(parts))
