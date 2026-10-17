"""The desktop app's brain.

It reuses the very same `Core`, modules, services and database as the Telegram
bot – only the transport differs: instead of Telegram messages, the UI talks
to the JSON API below, and `core.notify()` lands in an inbox + OS pop-ups.

Everything here runs on ONE asyncio thread (see runtime.py), so the SQLite
connection is never shared between threads.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets
import webbrowser
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..assistant import KEY_PREFIX, MODEL_LABEL, Assistant, AssistantError, chat_system, ideas_system, parse_ideas
from ..config import Settings
from ..core import ui
from ..core.app import Core
from ..gemini import MIN_KEY_LENGTH as GEMINI_MIN_KEY
from ..gemini import GeminiAssistant
from ..google_auth import CLIENT_ID_SUFFIX, YOUTUBE_SCOPE, build_auth_url, exchange_code, make_pkce
from ..modules import ALL_MODULES
from ..modules.drive import DriveModule
from ..modules.motivation import UPLOADS_GOAL, VIEWS_GOAL
from ..modules.videos import Video, VideoStep
from ..modules.youtube_stats import YouTubeStatsModule
from ..storage import Database
from . import __version__
from .autostart import Autostart
from .notifier import DesktopBot, Feed, show_os_notification

log = logging.getLogger(__name__)

OWNER = 1  # the desktop app has exactly one user; this id only satisfies Core.notify()
OPEN_HOSTS = {
    "drive.google.com",
    "docs.google.com",
    "www.youtube.com",
    "youtube.com",
    "studio.youtube.com",
    "console.cloud.google.com",  # the one-time setup guide
    "accounts.google.com",
    "myaccount.google.com",
    "console.anthropic.com",  # getting an API key for the assistant
    "aistudio.google.com",  # the free Gemini key
}
SIGN_IN_VALID_FOR = timedelta(minutes=10)
CHAT_LIMIT = 30  # messages kept and sent to the assistant
# The two assistants a person can use. The free one is listed (and offered) first.
PROVIDERS = {
    "gemini": {"label": GeminiAssistant.label, "free": True},
    "claude": {"label": MODEL_LABEL, "free": False},
}
KEY_SETTING = {"gemini": "assistant.gemini_key", "claude": "assistant.api_key"}  # "claude" kept for old installs
HTTP_FOR_KIND = {"key": 400, "billing": 402, "rate": 429, "refusal": 422, "connection": 502, "server": 502}
WINDOW_ACTIVE_FOR = timedelta(seconds=45)  # how long a "window is in front" heartbeat counts
RETRY_AFTER = timedelta(minutes=30)
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Request:
    body: dict[str, Any] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)


def build_settings(settings: Settings, data_dir: Path) -> Settings:
    """Desktop defaults on top of the environment: own data folder, no Telegram, all core modules on."""
    db_path = Path(os.environ["DB_PATH"]) if os.environ.get("DB_PATH") else data_dir / "creator_bot.db"
    return replace(settings, owner_chat_id=OWNER, disabled_modules=frozenset(), db_path=db_path)


def _local_now() -> datetime:
    return datetime.now().astimezone()


class DesktopApp:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        clock: Callable[[], datetime] | None = None,
        show_os: Callable[[str, str], Any] = show_os_notification,
        autostart: Autostart | None = None,
        open_url: Callable[[str], Any] = webbrowser.open,
        assistant_factory: Callable[[str], Assistant] = Assistant,
        gemini_factory: Callable[[str], GeminiAssistant] = GeminiAssistant,
    ) -> None:
        self._env_settings = settings  # what the environment / .env says
        self.db = db
        self.settings = self._effective_settings()
        self.port: int | None = None  # set once the local server is listening (the sign-in redirects back to it)
        self.google_transport: Any = None  # tests replace the network
        self._pending_sign_in: tuple[str, str, datetime] | None = None  # (state, verifier, expires)
        self._assistant_factory = assistant_factory  # Claude
        self._gemini_factory = gemini_factory
        self._assistant: Assistant | GeminiAssistant | None = None
        self._assistant_key: tuple[str, str] | None = None  # (provider, key) of the cached assistant
        self._chat: list[dict[str, str]] = []  # the conversation, kept while the app runs
        self._asking = asyncio.Lock()
        self.feed = Feed()
        self.show_os = show_os
        self.autostart = autostart or Autostart()
        self.open_url = open_url
        self.quit_callback: Callable[[], None] | None = None
        self._active_until: datetime | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._retry_at: dict[str, datetime] = {}
        self._busy: set[str] = set()

        self.core = Core(self.settings, db, clock or _local_now)
        self.core.bot = DesktopBot(self.feed, self.core.now, self._os_enabled, self.window_active, show_os)
        self.core.load(ALL_MODULES)

        self.videos = self.core.get("videos")
        self.routines = self.core.get("routines")
        self.ideas = self.core.get("ideas")
        self.motivation = self.core.get("motivation")
        self.stats = self.core.get("youtube_stats")  # optional integrations
        self.drive = self.core.get("drive")

    # -- plumbing ------------------------------------------------------------------
    def window_active(self) -> bool:
        return self._active_until is not None and self.core.now() < self._active_until

    def _os_enabled(self) -> bool:
        return bool(self.db.get("desktop.os_notifications", True))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run slow follow-ups (events, network) without making the click wait for them."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("Background task failed", exc_info=task.exception())

    async def idle(self) -> None:
        """Wait for background work (used by tests and on shutdown)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _emit(self, *events: tuple[str, dict[str, Any]]) -> None:
        async def run() -> None:
            for name, payload in events:
                await self.core.events.emit(name, **payload)

        self._spawn(run())

    async def handle(
        self, method: str, path: str, query: dict[str, str] | None = None, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """Dispatch one API call. Returns (HTTP status, JSON-able data)."""
        known_path = False
        for route_method, pattern, name in ROUTES:
            match = pattern.fullmatch(path)
            if not match:
                continue
            known_path = True
            if route_method != method:
                continue
            try:
                result = await getattr(self, name)(Request(body or {}, query or {}), *match.groups())
            except ApiError as error:
                return error.status, {"error": error.message}
            except Exception:
                log.exception("API call failed: %s %s", method, path)
                return 500, {"error": "משהו השתבש. אפשר לנסות שוב."}
            return result if isinstance(result, tuple) else (200, result)
        return (405, {"error": "method not allowed"}) if known_path else (404, {"error": "not found"})

    # -- JSON views ----------------------------------------------------------------------
    def _step_json(self, step: VideoStep, current: VideoStep | None, day: date) -> dict[str, Any]:
        state = "done" if step.done_on else "current" if step is current else "todo"
        return {
            "key": step.key,
            "name": step.info.name,
            "verb": step.info.verb,
            "emoji": step.info.emoji,
            "due": step.due.isoformat(),
            "due_label": ui.fmt_day(step.due, day),
            "done_on": step.done_on.isoformat() if step.done_on else None,
            "state": state,
            "late_days": max(0, (day - step.due).days) if state == "current" else 0,
        }

    def _video_json(self, video: Video, day: date) -> dict[str, Any]:
        folder = None
        if self.drive is not None:
            row = self.db.query_one("SELECT url FROM drive_video_folders WHERE video_id = ?", (video.id,))
            folder = row["url"] if row else None
        return {
            "id": video.id,
            "title": video.title,
            "publish_date": video.publish_date.isoformat(),
            "published_on": video.published_on.isoformat() if video.published_on else None,
            "steps": [self._step_json(step, video.current, day) for step in video.steps],
            "drive_url": folder,
        }

    def _week_json(self, day: date) -> dict[str, Any]:
        progress = self.motivation.week_progress(day)
        return {
            "done": progress.done,
            "goal": progress.goal,
            "met": progress.met,
            "scheduled": progress.scheduled,
            "missing": max(0, progress.goal - progress.done - progress.scheduled),
            "days_left": (progress.end - day).days + 1,
            "streak": self.motivation.streak(day),
        }

    def _views_json(self, day: date) -> dict[str, int] | None:
        progress = self.motivation.views_progress(day)
        return {"gained": progress[0], "goal": progress[1]} if progress else None

    def _channel_json(self, day: date) -> dict[str, Any] | None:
        if self.stats is None:
            return None
        latest = self.stats.service.on_or_before(day)
        if latest is None:
            return {"empty": True}
        previous = self.stats.service.on_or_before(date.fromisoformat(latest["day"]) - timedelta(days=1))

        def delta(key: str) -> int | None:
            return latest[key] - previous[key] if previous and previous[key] is not None else None

        return {
            "title": self.stats.channel_title,
            "day": latest["day"],
            "views": latest["views"],
            "subscribers": latest["subscribers"],
            "videos": latest["videos"],
            "views_delta": delta("views"),
            "subscribers_delta": delta("subscribers") if latest["subscribers"] is not None else None,
        }

    # -- today ---------------------------------------------------------------------------
    async def today(self, req: Request) -> dict[str, Any]:
        day = self.core.today()
        service = self.videos.service
        tasks = []
        for video, step in service.due(day):
            tasks.append({"video_id": video.id, "title": video.title, **self._step_json(step, step, day)})
        upcoming = [
            {"video_id": video.id, "title": video.title, **self._step_json(step, step, day)}
            for video, step in service.upcoming(day)
        ]
        routines = [
            {"id": r.id, "title": r.title, "done": self.routines.service.is_done(r.id, day)}
            for r in self.routines.service.due_on(day)
        ]
        steps_done = self.db.query_one("SELECT COUNT(*) AS n FROM video_steps WHERE done_on = ?", (day.isoformat(),))[
            "n"
        ]
        open_routines = sum(1 for r in routines if not r["done"])
        ideas_open = self.ideas.service.count_open()
        return {
            "date": day.isoformat(),
            "quote": self.motivation.quote_for(day),
            "week": self._week_json(day),
            "views": self._views_json(day),
            "channel": self._channel_json(day),
            "video_tasks": tasks,
            "routines": routines,
            "upcoming": upcoming,
            "open_count": len(tasks) + open_routines,
            "done_today": steps_done + (len(routines) - open_routines),
            "ideas_open": ideas_open,
            "nudge_idea": ideas_open > 0 and not service.active(),
            "unread": self.feed.unread,
        }

    # -- videos --------------------------------------------------------------------------
    def _parse_future_day(self, value: Any) -> date:
        try:
            day = date.fromisoformat(str(value))
        except ValueError:
            raise ApiError(400, "תאריך לא תקין") from None
        if day < self.core.today():
            raise ApiError(400, "התאריך כבר עבר")
        return day

    @staticmethod
    def _text(value: Any, name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ApiError(400, f"חסר {name}")
        return text[:limit]

    async def videos_list(self, req: Request) -> dict[str, Any]:
        day = self.core.today()
        published_ids = self.db.query(
            "SELECT id FROM videos WHERE published_on IS NOT NULL ORDER BY published_on DESC, id DESC LIMIT 10"
        )
        return {
            "active": [self._video_json(v, day) for v in self.videos.service.active()],
            "published": [self._video_json(self.videos.service.get(row["id"]), day) for row in published_ids],
        }

    async def create_video(self, req: Request) -> tuple[int, Any]:
        title = self._text(req.body.get("title"), "שם לסרטון", 100)
        publish = self._parse_future_day(req.body.get("date"))
        video = self.videos.service.create(title, publish, self.core.today())
        self._emit(("video.created", {"video": video, "idea_id": None}))
        return 201, self._video_json(video, self.core.today())

    async def video_done(self, req: Request, video_id: str) -> dict[str, Any]:
        result = self.videos.service.complete_step(int(video_id), str(req.body.get("step")), self.core.today())
        if result is None:  # already done (double click): nothing to do
            return {"changed": False}
        video, step, next_step = result
        events = [("video.step_done", {"video": video, "step": step, "next_step": next_step})]
        if next_step is None:
            events.append(("video.published", {"video": video}))
        self._emit(*events)
        return {"changed": True, "published": next_step is None, "message": f"✅ {step.info.name} – בוצע!"}

    async def video_snooze(self, req: Request, video_id: str) -> dict[str, Any]:
        video = self.videos.service.snooze(int(video_id), str(req.body.get("step")), self.core.today())
        return {"changed": video is not None}

    async def video_delete(self, req: Request, video_id: str) -> dict[str, Any]:
        if not self.videos.service.delete(int(video_id)):
            raise ApiError(404, "הסרטון לא נמצא")
        return {"ok": True}

    # -- routines ------------------------------------------------------------------------
    def _routine_json(self, routine: Any, day: date) -> dict[str, Any]:
        return {
            "id": routine.id,
            "title": routine.title,
            "weekdays": sorted(routine.weekdays),
            "days_label": self.routines.fmt_days(routine.weekdays),
            "due_today": day.weekday() in routine.weekdays,
            "done_today": self.routines.service.is_done(routine.id, day),
        }

    async def routines_list(self, req: Request) -> dict[str, Any]:
        day = self.core.today()
        return {
            "routines": [self._routine_json(r, day) for r in self.routines.service.all()],
            "week_order": self.routines.week_order,
            "weekday_letters": list(ui.WEEKDAY_LETTERS),
        }

    async def create_routine(self, req: Request) -> tuple[int, Any]:
        title = self._text(req.body.get("title"), "שם למשימה", 80)
        days = req.body.get("weekdays")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
            raise ApiError(400, "צריך לבחור לפחות יום אחד")
        day = self.core.today()
        return 201, self._routine_json(self.routines.service.add(title, set(days), day), day)

    async def routine_done(self, req: Request, routine_id: str) -> dict[str, Any]:
        routine = self.routines.service.get(int(routine_id))
        if routine is None:
            raise ApiError(404, "המשימה לא נמצאה")
        day = self.core.today()
        changed = self.routines.service.mark_done(routine.id, day)
        if changed:
            self._emit(("routine.done", {"routine": routine, "day": day}))
        return {"changed": changed}

    async def routine_delete(self, req: Request, routine_id: str) -> dict[str, Any]:
        if not self.routines.service.delete(int(routine_id)):
            raise ApiError(404, "המשימה לא נמצאה")
        return {"ok": True}

    # -- ideas ---------------------------------------------------------------------------
    async def ideas_list(self, req: Request) -> dict[str, Any]:
        return {"ideas": [{"id": i.id, "text": i.text} for i in self.ideas.service.open(limit=200)]}

    async def create_idea(self, req: Request) -> tuple[int, Any]:
        idea = self.ideas.service.add(self._text(req.body.get("text"), "טקסט לרעיון", 300), self.core.today())
        return 201, {"id": idea.id, "text": idea.text}

    async def idea_to_video(self, req: Request, idea_id: str) -> tuple[int, Any]:
        idea = self.ideas.service.get(int(idea_id))
        if idea is None:
            raise ApiError(404, "הרעיון לא נמצא")
        publish = self._parse_future_day(req.body.get("date"))
        day = self.core.today()
        video = self.videos.service.create(idea.text[:100], publish, day)
        self.ideas.service.mark_used(idea.id, video.id)  # now, not in the background: the list refreshes next
        self._emit(("video.created", {"video": video, "idea_id": idea.id}))
        return 201, self._video_json(video, day)

    async def idea_delete(self, req: Request, idea_id: str) -> dict[str, Any]:
        if not self.ideas.service.delete(int(idea_id)):
            raise ApiError(404, "הרעיון לא נמצא")
        return {"ok": True}

    # -- goals ---------------------------------------------------------------------------
    @staticmethod
    def _int(value: Any, name: str, low: int, high: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ApiError(400, f"{name}: מספר בין {low:,} ל-{high:,}")
        return value

    async def goals(self, req: Request) -> dict[str, Any]:
        day = self.core.today()
        return {
            "uploads_per_week": self.motivation.uploads_goal,
            "views_per_month": self.motivation.views_goal,
            "week": self._week_json(day),
            "views": self._views_json(day),
            "stats_enabled": self.stats is not None,
        }

    async def set_goals(self, req: Request) -> dict[str, Any]:
        body = req.body
        if "uploads_per_week" in body:
            self.db.set(UPLOADS_GOAL, self._int(body["uploads_per_week"], "יעד העלאות", 1, 14))
        if "views_per_month" in body:
            views = self._int(body["views_per_month"], "יעד צפיות", 0, 1_000_000_000)
            self.db.set(VIEWS_GOAL, views or None)
        return await self.goals(req)

    # -- inbox ---------------------------------------------------------------------------
    async def feed_list(self, req: Request) -> dict[str, Any]:
        if req.query.get("focused") == "1":
            self._active_until = self.core.now() + WINDOW_ACTIVE_FOR
        return self.feed.to_json()

    async def feed_read(self, req: Request) -> dict[str, Any]:
        self.feed.mark_all_read()
        return self.feed.to_json()

    # -- settings ------------------------------------------------------------------------
    def reminder_times(self) -> tuple[time, time]:
        def read(key: str, default: str) -> time:
            hours, minutes = (self.db.get(key) or default).split(":")
            return time(int(hours), int(minutes))

        return read("desktop.morning", "09:00"), read("desktop.evening", "20:30")

    async def settings_get(self, req: Request) -> dict[str, Any]:
        morning, evening = self.reminder_times()
        return {
            "morning": morning.strftime("%H:%M"),
            "evening": evening.strftime("%H:%M"),
            "os_notifications": self._os_enabled(),
            "autostart": self.autostart.is_enabled(),
            "youtube": self.stats is not None,
            "drive": self.drive is not None,
            "data_dir": str(self.settings.db_path.parent),
            "version": __version__,
        }

    async def settings_put(self, req: Request) -> dict[str, Any]:
        body = req.body
        for key, field_name in (("morning", "desktop.morning"), ("evening", "desktop.evening")):
            if key in body:
                if not isinstance(body[key], str) or not _HHMM.match(body[key]):
                    raise ApiError(400, "שעה לא תקינה (HH:MM)")
                self.db.set(field_name, body[key])
        if "os_notifications" in body:
            self.db.set("desktop.os_notifications", bool(body["os_notifications"]))
        if "autostart" in body:
            try:
                if body["autostart"]:
                    self.autostart.enable()
                else:
                    self.autostart.disable()
            except OSError:
                log.exception("Could not change the autostart setting")
                raise ApiError(500, "לא הצלחתי לשנות את ההפעלה האוטומטית") from None
        return await self.settings_get(req)

    async def test_notification(self, req: Request) -> dict[str, Any]:
        text = "🔔 התראת בדיקה\nאם אתה רואה את זה – ההתראות עובדות!"
        self.feed.add(text, None, self.core.now())
        shown = await asyncio.to_thread(self.show_os, "🔔 התראת בדיקה", "אם אתה רואה את זה – ההתראות עובדות!")
        return {"shown": bool(shown)}

    # -- optional integrations -------------------------------------------------------------
    async def stats_refresh(self, req: Request) -> dict[str, Any]:
        if self.stats is None:
            raise ApiError(404, "נתוני הערוץ לא מוגדרים (ראה README)")
        try:
            await self.stats.refresh()
        except (httpx.HTTPError, LookupError, KeyError, ValueError):
            log.exception("Could not refresh YouTube stats")
            raise ApiError(502, "לא הצלחתי למשוך נתונים מיוטיוב. כדאי לבדוק את החיבור (בהגדרות) או את המפתח.") from None
        return {"channel": self._channel_json(self.core.today())}

    async def drive_backup(self, req: Request) -> dict[str, Any]:
        if self.drive is None:
            raise ApiError(404, "גוגל דרייב לא מוגדר (ראה README)")
        try:
            backups = await self.drive.backup(self.core.today())
        except httpx.HTTPError:
            log.exception("Drive backup failed")
            raise ApiError(502, "הגיבוי נכשל. כדאי לבדוק את הגדרות GOOGLE_ ב-.env.") from None
        return {"url": backups["webViewLink"]}

    async def open_link(self, req: Request) -> dict[str, Any]:
        url = str(req.body.get("url", ""))
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "") not in OPEN_HOSTS:
            raise ApiError(400, "קישור לא מותר")
        self.open_url(url)
        return {"ok": True}

    async def ping(self, req: Request) -> dict[str, Any]:
        return {"app": "creator-desk", "version": __version__}

    async def quit(self, req: Request) -> dict[str, Any]:
        if self.quit_callback:
            self.quit_callback()
        return {"ok": True}

    # -- Google sign-in (YouTube stats + Drive) ----------------------------------------------
    def _google_from_env(self) -> bool:
        env = self._env_settings
        return bool(env.google_client_id and env.google_client_secret and env.google_refresh_token)

    def _effective_settings(self) -> Settings:
        """The environment's settings, plus the Google sign-in saved by the app (unless .env has its own)."""
        env = self._env_settings
        if self._google_from_env():
            return env
        saved = {key: self.db.get(f"google.{key}") for key in ("client_id", "client_secret", "refresh_token", "scopes")}
        return replace(
            env,
            google_client_id=saved["client_id"] or env.google_client_id,
            google_client_secret=saved["client_secret"] or env.google_client_secret,
            google_refresh_token=saved["refresh_token"] or env.google_refresh_token,
            google_scopes=frozenset((saved["scopes"] or "").split()),
        )

    def reload_integrations(self) -> None:
        """Apply a changed sign-in right away: unload and load again the two optional modules."""
        for name in ("youtube_stats", "drive"):
            self.core.unload(name)
        self.settings = self._effective_settings()
        self.core.settings = self.settings
        self.core.load([YouTubeStatsModule, DriveModule])
        order = {cls.name: position for position, cls in enumerate(ALL_MODULES)}  # keep the digest order stable
        self.core.modules = dict(sorted(self.core.modules.items(), key=lambda item: order[item[0]]))
        self.stats, self.drive = self.core.get("youtube_stats"), self.core.get("drive")
        self._retry_at.clear()

    def _redirect_uri(self) -> str:
        if self.port is None:
            raise ApiError(500, "השרת המקומי עוד לא מוכן")
        return f"http://127.0.0.1:{self.port}/oauth/callback"

    def _save_client(self, client_id: Any, client_secret: Any) -> None:
        client_id, client_secret = str(client_id or "").strip(), str(client_secret or "").strip()
        if not client_id.endswith(CLIENT_ID_SUFFIX):
            raise ApiError(400, f"מזהה הלקוח (Client ID) אמור להסתיים ב-{CLIENT_ID_SUFFIX}")
        if len(client_secret) < 10:
            raise ApiError(400, "חסר סוד הלקוח (Client secret)")
        if client_id != self.db.get("google.client_id"):
            for key in ("refresh_token", "scopes"):  # a sign-in belongs to the client that made it
                self.db.set(f"google.{key}", None)
        self.db.set("google.client_id", client_id)
        self.db.set("google.client_secret", client_secret)
        self.reload_integrations()  # a changed client means the old sign-in no longer applies

    async def google_get(self, req: Request) -> dict[str, Any]:
        settings = self.settings
        return {
            "source": "env" if self._google_from_env() else "app",
            "client_id": settings.google_client_id or "",
            "has_client": bool(settings.google_client_id and settings.google_client_secret),
            "connected": bool(settings.google_client_id and settings.google_refresh_token),
            "youtube": self.stats is not None,
            "drive": self.drive is not None,
            "waiting": self._pending_sign_in is not None and self.core.now() < self._pending_sign_in[2],
            "redirect_uri": f"http://127.0.0.1:{self.port}/oauth/callback" if self.port else None,
        }

    async def google_connect(self, req: Request) -> dict[str, Any]:
        """Save the client (if given), then send the user to Google's consent page in their own browser."""
        if self._google_from_env():
            raise ApiError(409, "החיבור מוגדר דרך קובץ .env")
        if "client_id" in req.body or "client_secret" in req.body:
            self._save_client(req.body.get("client_id"), req.body.get("client_secret"))
        client_id = self.db.get("google.client_id")
        if not client_id or not self.db.get("google.client_secret"):
            raise ApiError(400, "צריך להזין מזהה לקוח וסוד לקוח")
        verifier, challenge = make_pkce()
        state = secrets.token_urlsafe(24)
        self._pending_sign_in = (state, verifier, self.core.now() + SIGN_IN_VALID_FOR)
        url = build_auth_url(client_id, self._redirect_uri(), state, challenge)
        self.open_url(url)  # the system browser: Google refuses to sign in inside embedded windows
        return {"url": url}

    async def oauth_callback(self, req: Request) -> dict[str, Any]:
        """Google sends the browser back here with a one-time code. Only a sign-in WE started is accepted."""
        pending, self._pending_sign_in = self._pending_sign_in, None  # one attempt per sign-in
        state = req.query.get("state", "")
        if pending is None or self.core.now() > pending[2] or not hmac.compare_digest(state, pending[0]):
            raise ApiError(400, "הבקשה פגה או לא תקינה. כדאי להתחיל מחדש מתוך האפליקציה.")
        code = req.query.get("code")
        if req.query.get("error") or not code:
            raise ApiError(400, "החיבור בוטל בגוגל. אפשר לנסות שוב מתוך האפליקציה.")
        try:
            tokens = await exchange_code(
                self.db.get("google.client_id"),
                self.db.get("google.client_secret"),
                code,
                pending[1],
                self._redirect_uri(),
                self.google_transport,
            )
        except httpx.HTTPError:
            log.exception("Google sign-in failed")
            raise ApiError(
                502, "גוגל דחתה את החיבור. כדאי לבדוק שמזהה הלקוח והסוד נכונים ושהסוג הוא Desktop app."
            ) from None
        if not tokens.get("refresh_token"):
            raise ApiError(400, "גוגל לא החזירה הרשאה קבועה. נסה שוב, ובמסך האישור השאר את כל ההרשאות מסומנות.")
        self.db.set("google.refresh_token", tokens["refresh_token"])
        self.db.set("google.scopes", str(tokens.get("scope", "")))
        self.reload_integrations()
        self._spawn(self._after_sign_in())
        return {"message": "✅ החיבור הושלם! אפשר לסגור את החלון הזה ולחזור ל-Creator Desk."}

    async def _after_sign_in(self) -> None:
        granted = self.settings.google_scopes
        missing = [name for name, module in (("יוטיוב", self.stats), ("דרייב", self.drive)) if module is None]
        text = "🔌 <b>חובר לחשבון גוגל</b>"
        if missing:
            text += f"\nלא ניתנה הרשאה ל: {', '.join(missing)}. אפשר להתחבר מחדש ולסמן את כל ההרשאות."
        await self.core.notify(text)
        if self.stats is not None and YOUTUBE_SCOPE in granted:
            try:
                await self.stats.refresh()
            except (httpx.HTTPError, LookupError, KeyError, ValueError):
                log.exception("First YouTube refresh after sign-in failed")

    async def google_disconnect(self, req: Request) -> dict[str, Any]:
        if self._google_from_env():
            raise ApiError(409, "החיבור מוגדר דרך קובץ .env")
        for key in ("client_id", "client_secret", "refresh_token", "scopes"):
            self.db.set(f"google.{key}", None)
        self._pending_sign_in = None
        self.reload_integrations()
        return await self.google_get(req)

    # -- AI assistant ---------------------------------------------------------------------------
    def _provider_key(self, provider: str) -> tuple[str | None, str | None]:
        """(key, where it came from) for one provider: a key saved in the app wins over the environment's."""
        saved = self.db.get(KEY_SETTING[provider])
        if saved:
            return saved, "app"
        env_key = self._env_settings.gemini_api_key if provider == "gemini" else self._env_settings.anthropic_api_key
        return (env_key, "env") if env_key else (None, None)

    def _active_provider(self) -> str:
        """The assistant in use: the one chosen last, if it still has a key, otherwise any that has one."""
        chosen = self.db.get("assistant.provider")
        order = ([chosen] if chosen in PROVIDERS else []) + list(PROVIDERS)
        return next(
            (name for name in order if self._provider_key(name)[0]), chosen if chosen in PROVIDERS else "gemini"
        )

    def _assistant_key_info(self) -> tuple[str | None, str | None]:
        return self._provider_key(self._active_provider())

    def _make_assistant(self, provider: str, key: str) -> Assistant | GeminiAssistant:
        return self._gemini_factory(key) if provider == "gemini" else self._assistant_factory(key)

    def _get_assistant(self) -> Assistant | GeminiAssistant:
        provider = self._active_provider()
        key, _ = self._provider_key(provider)
        if not key:
            raise ApiError(409, "צריך קודם להפעיל את העוזר: להזין מפתח API (בהגדרות או במסך העוזר).")
        if self._assistant is None or (provider, key) != self._assistant_key:
            self._assistant, self._assistant_key = self._make_assistant(provider, key), (provider, key)
        return self._assistant

    @staticmethod
    def _ask_error(error: AssistantError) -> ApiError:
        return ApiError(HTTP_FOR_KIND.get(error.kind, 502), error.message)

    def _channel_context(self) -> str:
        """A short, fresh snapshot of the channel for the model. Only what the user already sees in the app."""
        day = self.core.today()
        service = self.videos.service
        lines = [f"הקשר על הערוץ (נכון ל-{day.isoformat()}):"]
        week = self._week_json(day)
        lines.append(
            f"- יעד העלאות שבועי: {week['goal']}. פורסמו השבוע: {week['done']}. רצף שבועות ביעד: {week['streak']}."
        )
        active = service.active()[:10]
        if active:
            lines.append("- סרטונים בתהליך:")
            for video in active:
                step = video.current
                stage = f"שלב נוכחי: {step.info.name} ביום {step.due.isoformat()}" if step else "מוכן"
                lines.append(f'  • "{video.title}" (פרסום ב-{video.publish_date.isoformat()}, {stage})')
        published = self.db.query(
            "SELECT id FROM videos WHERE published_on IS NOT NULL ORDER BY published_on DESC LIMIT 8"
        )
        if published:
            lines.append("- סרטונים שפורסמו לאחרונה:")
            for row in published:
                video = service.get(row["id"])
                lines.append(f'  • "{video.title}" ({video.published_on.isoformat()})')
        ideas = self.ideas.service.open(limit=15)
        if ideas:
            lines.append("- רעיונות שמורים שעוד לא הפכו לסרטון:")
            lines += [f'  • "{idea.text}"' for idea in ideas]
        channel = self._channel_json(day)
        if channel and not channel.get("empty"):
            subs = f", {channel['subscribers']:,} מנויים" if channel["subscribers"] is not None else ""
            lines.append(f"- נתוני הערוץ (יוטיוב): {channel['views']:,} צפיות{subs}, {channel['videos']:,} סרטונים.")
        return "\n".join(lines)

    async def assistant_get(self, req: Request) -> dict[str, Any]:
        provider = self._active_provider()
        key, source = self._provider_key(provider)
        return {
            "configured": bool(key),
            "provider": provider,
            "source": source,
            "key_hint": f"…{key[-4:]}" if key else None,
            "model": PROVIDERS[provider]["label"],
            "providers": [
                {
                    "id": name,
                    "label": info["label"],
                    "free": info["free"],
                    "configured": bool(self._provider_key(name)[0]),
                }
                for name, info in PROVIDERS.items()
            ],
            "messages": [{"role": m["role"], "text": m["content"]} for m in self._chat],
            "busy": self._asking.locked(),
        }

    async def assistant_put(self, req: Request) -> dict[str, Any]:
        provider = str(req.body.get("provider") or "claude")
        if provider not in PROVIDERS:
            raise ApiError(400, "ספק לא מוכר")
        key = str(req.body.get("api_key") or "").strip()
        if provider == "claude" and not (key.startswith(KEY_PREFIX) and len(key) >= 20):
            raise ApiError(400, f"מפתח ה-API אמור להתחיל ב-{KEY_PREFIX}")
        if provider == "gemini" and (len(key) < GEMINI_MIN_KEY or any(ch.isspace() for ch in key)):
            raise ApiError(400, "המפתח נראה קצר או לא תקין. כדאי להעתיק אותו שוב מ-Google AI Studio.")
        try:
            await self._make_assistant(provider, key).check_key()  # nothing is saved unless the key really works
        except AssistantError as error:
            raise self._ask_error(error) from None
        self.db.set(KEY_SETTING[provider], key)
        self.db.set("assistant.provider", provider)
        self._assistant = None
        return await self.assistant_get(req)

    async def assistant_delete(self, req: Request) -> dict[str, Any]:
        provider = self._active_provider()
        if self._provider_key(provider)[1] == "env":
            raise ApiError(409, "המפתח מוגדר דרך קובץ .env")
        self.db.set(KEY_SETTING[provider], None)
        self._assistant = None
        self._chat.clear()
        return await self.assistant_get(req)

    async def assistant_chat(self, req: Request) -> dict[str, Any]:
        text = str(req.body.get("message") or "").strip()
        if not text:
            raise ApiError(400, "כתוב הודעה")
        if len(text) > 4000:
            raise ApiError(400, "ההודעה ארוכה מדי (עד 4,000 תווים)")
        assistant = self._get_assistant()
        if self._asking.locked():
            raise ApiError(409, "העוזר עדיין עונה על ההודעה הקודמת")
        async with self._asking:
            history = [*self._chat, {"role": "user", "content": text}][-CHAT_LIMIT:]
            while history and history[0]["role"] != "user":  # a conversation must open with the user
                history.pop(0)
            try:
                reply = await assistant.ask(chat_system(self._channel_context()), history, effort="low")
            except AssistantError as error:
                raise self._ask_error(error) from None  # the question is not kept, so it can simply be sent again
            self._chat = [*history, {"role": "assistant", "content": reply}]
        return {"reply": reply}

    async def assistant_clear(self, req: Request) -> dict[str, Any]:
        self._chat.clear()
        return {"ok": True}

    async def assistant_ideas(self, req: Request) -> dict[str, Any]:
        topic = str(req.body.get("topic") or "").strip()[:300]
        avoid = [str(title)[:200] for title in (req.body.get("avoid") or [])[:40] if isinstance(title, str)]
        assistant = self._get_assistant()
        if self._asking.locked():
            raise ApiError(409, "העוזר עדיין עונה על בקשה קודמת")
        context = self._channel_context()
        if avoid:
            context += "\n- רעיונות שכבר הוצעו עכשיו ואין לחזור עליהם:\n" + "\n".join(
                f'  • "{title}"' for title in avoid
            )
        prompt = f"רעיונות לסרטונים בנושא: {topic}" if topic else "תן לי רעיונות לסרטונים הבאים בערוץ."
        async with self._asking:
            try:
                reply = await assistant.ask(
                    ideas_system(context, 5), [{"role": "user", "content": prompt}], effort="medium"
                )
            except AssistantError as error:
                raise self._ask_error(error) from None
        return {"ideas": [{"title": idea.title, "why": idea.why} for idea in parse_ideas(reply)]}

    # -- reminders (called every ~30 s by the runtime) -------------------------------------
    async def tick(self) -> None:
        now = self.core.now()
        day = now.date()
        morning, evening = self.reminder_times()
        clock = now.time()
        for kind, at in (("morning", morning), ("evening", evening)):
            sent_key = f"desktop.sent.{kind}"
            if clock < at or self.db.get(sent_key) == day.isoformat():
                continue
            self.db.set(sent_key, day.isoformat())  # first: a crash must not make it repeat all day
            # Opened after the evening time? "Good morning" would be silly: skip it.
            if kind == "morning" and clock >= evening:
                continue
            await self.core.send_digest(kind)
        self._background_jobs(now)

    def _background_jobs(self, now: datetime) -> None:
        day = now.date()
        if self.stats is not None:
            latest = self.stats.service.on_or_before(day)
            if (latest is None or latest["day"] != day.isoformat()) and self._ready("stats", now):
                self._run_job("stats", now, self.stats.refresh())
        if (
            self.drive is not None
            and self.db.get("desktop.last_backup") != day.isoformat()
            and self._ready("backup", now)
        ):
            self._run_job("backup", now, self._backup_and_remember(day))

    def _ready(self, name: str, now: datetime) -> bool:
        return name not in self._busy and now >= self._retry_at.get(name, now)

    def _run_job(self, name: str, now: datetime, coro: Awaitable[Any]) -> None:
        self._busy.add(name)

        async def run() -> None:
            try:
                await coro
            except (httpx.HTTPError, LookupError, KeyError, ValueError):
                log.exception("Background job %s failed – will retry later", name)
                self._retry_at[name] = self.core.now() + RETRY_AFTER
            finally:
                self._busy.discard(name)

        self._spawn(run())

    async def _backup_and_remember(self, day: date) -> None:
        await self.drive.backup(day)
        self.db.set("desktop.last_backup", day.isoformat())


ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    (method, re.compile(pattern), name)
    for method, pattern, name in [
        ("GET", r"/api/ping", "ping"),
        ("GET", r"/api/today", "today"),
        ("GET", r"/api/videos", "videos_list"),
        ("POST", r"/api/videos", "create_video"),
        ("POST", r"/api/videos/(\d+)/done", "video_done"),
        ("POST", r"/api/videos/(\d+)/snooze", "video_snooze"),
        ("DELETE", r"/api/videos/(\d+)", "video_delete"),
        ("GET", r"/api/routines", "routines_list"),
        ("POST", r"/api/routines", "create_routine"),
        ("POST", r"/api/routines/(\d+)/done", "routine_done"),
        ("DELETE", r"/api/routines/(\d+)", "routine_delete"),
        ("GET", r"/api/ideas", "ideas_list"),
        ("POST", r"/api/ideas", "create_idea"),
        ("POST", r"/api/ideas/(\d+)/video", "idea_to_video"),
        ("DELETE", r"/api/ideas/(\d+)", "idea_delete"),
        ("GET", r"/api/goals", "goals"),
        ("PUT", r"/api/goals", "set_goals"),
        ("GET", r"/api/feed", "feed_list"),
        ("POST", r"/api/feed/read", "feed_read"),
        ("GET", r"/api/settings", "settings_get"),
        ("PUT", r"/api/settings", "settings_put"),
        ("POST", r"/api/test-notification", "test_notification"),
        ("POST", r"/api/stats/refresh", "stats_refresh"),
        ("POST", r"/api/drive/backup", "drive_backup"),
        ("GET", r"/api/assistant", "assistant_get"),
        ("PUT", r"/api/assistant", "assistant_put"),
        ("DELETE", r"/api/assistant", "assistant_delete"),
        ("POST", r"/api/assistant/chat", "assistant_chat"),
        ("DELETE", r"/api/assistant/chat", "assistant_clear"),
        ("POST", r"/api/assistant/ideas", "assistant_ideas"),
        ("GET", r"/api/google", "google_get"),
        ("POST", r"/api/google/connect", "google_connect"),
        ("DELETE", r"/api/google", "google_disconnect"),
        ("GET", r"/oauth/callback", "oauth_callback"),
        ("POST", r"/api/open", "open_link"),
        ("POST", r"/api/quit", "quit"),
    ]
]
