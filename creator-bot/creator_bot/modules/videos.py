"""🎬 Videos: plan a video, auto-schedule filming → editing → upload, remind on time.

Events emitted:
  video.created(video, idea_id)          – a new video was planned
  video.step_done(video, step, next_step) – a stage was completed
  video.published(video)                  – the last stage (upload) was completed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from telegram import InlineKeyboardMarkup, Update

from ..core import ui
from ..core.dates import parse_date
from ..core.module import Command, Ctx, DigestSection, MenuButton, Module
from ..storage import Database


@dataclass(frozen=True)
class StepDef:
    key: str
    name: str  # noun, e.g. "צילום"
    verb: str  # infinitive, e.g. "לצלם"
    emoji: str
    offset_days: int  # when it is due, relative to the publish date


# The production pipeline. To add a stage (script, thumbnail...) add a line here.
STEPS: tuple[StepDef, ...] = (
    StepDef("film", "צילום", "לצלם", "🎥", -4),
    StepDef("edit", "עריכה", "לערוך", "✂️", -2),
    StepDef("upload", "העלאה", "להעלות", "🚀", 0),
)
STEP_BY_KEY = {step.key: step for step in STEPS}

SCHEMA = [
    """
    CREATE TABLE videos (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        created_on TEXT NOT NULL,
        published_on TEXT
    );
    CREATE TABLE video_steps (
        video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        step TEXT NOT NULL,
        due_on TEXT NOT NULL,
        done_on TEXT,
        PRIMARY KEY (video_id, position)
    );
    """,
]


@dataclass
class VideoStep:
    position: int
    key: str
    due: date
    done_on: date | None

    @property
    def info(self) -> StepDef:
        return STEP_BY_KEY.get(self.key) or StepDef(self.key, self.key, self.key, "•", 0)


@dataclass
class Video:
    id: int
    title: str
    created_on: date
    published_on: date | None
    steps: list[VideoStep]

    @property
    def current(self) -> VideoStep | None:
        return next((step for step in self.steps if step.done_on is None), None)

    @property
    def publish_date(self) -> date:
        return self.steps[-1].due


def plan_steps(publish: date, today: date) -> list[tuple[StepDef, date]]:
    """Count back from the publish date; never schedule in the past or out of order."""
    planned, previous = [], today
    for step in STEPS:
        due = max(publish + timedelta(days=step.offset_days), previous)
        planned.append((step, due))
        previous = due
    return planned


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class VideoService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, title: str, publish: date, today: date) -> Video:
        cursor = self.db.execute("INSERT INTO videos (title, created_on) VALUES (?, ?)", (title, today.isoformat()))
        video_id = cursor.lastrowid
        for position, (step, due) in enumerate(plan_steps(publish, today)):
            self.db.execute(
                "INSERT INTO video_steps (video_id, position, step, due_on) VALUES (?, ?, ?, ?)",
                (video_id, position, step.key, due.isoformat()),
            )
        return self.get(video_id)

    def get(self, video_id: int) -> Video | None:
        row = self.db.query_one("SELECT * FROM videos WHERE id = ?", (video_id,))
        if row is None:
            return None
        steps = [
            VideoStep(r["position"], r["step"], date.fromisoformat(r["due_on"]), _date(r["done_on"]))
            for r in self.db.query("SELECT * FROM video_steps WHERE video_id = ? ORDER BY position", (video_id,))
        ]
        return Video(row["id"], row["title"], date.fromisoformat(row["created_on"]), _date(row["published_on"]), steps)

    def active(self) -> list[Video]:
        rows = self.db.query("SELECT id FROM videos WHERE published_on IS NULL")
        videos = [self.get(row["id"]) for row in rows]
        return sorted(videos, key=lambda v: (v.current.due if v.current else v.publish_date, v.id))

    def due(self, day: date) -> list[tuple[Video, VideoStep]]:
        """The current stage of every video that is due today or overdue."""
        return [(v, v.current) for v in self.active() if v.current and v.current.due <= day]

    def upcoming(self, day: date, days: int = 3) -> list[tuple[Video, VideoStep]]:
        end = day + timedelta(days=days)
        return [(v, v.current) for v in self.active() if v.current and day < v.current.due <= end]

    def complete_step(
        self, video_id: int, step_key: str, today: date
    ) -> tuple[Video, VideoStep, VideoStep | None] | None:
        """Mark the current stage done. Returns None if it is not the current stage
        (e.g. a double tap), so a stage is never skipped by accident."""
        video = self.get(video_id)
        current = video.current if video else None
        if current is None or current.key != step_key:
            return None
        self.db.execute(
            "UPDATE video_steps SET done_on = ? WHERE video_id = ? AND position = ?",
            (today.isoformat(), video_id, current.position),
        )
        video = self.get(video_id)
        next_step = video.current
        if next_step is None:
            self.db.execute("UPDATE videos SET published_on = ? WHERE id = ?", (today.isoformat(), video_id))
            video = self.get(video_id)
        return video, video.steps[current.position], next_step

    def snooze(self, video_id: int, step_key: str, today: date, days: int = 1) -> Video | None:
        """Move the current stage to `today + days`, pushing later stages if needed."""
        video = self.get(video_id)
        current = video.current if video else None
        if current is None or current.key != step_key:
            return None
        previous = today + timedelta(days=days)
        for step in video.steps[current.position :]:
            due = previous if step is current else max(step.due, previous)
            self.db.execute(
                "UPDATE video_steps SET due_on = ? WHERE video_id = ? AND position = ?",
                (due.isoformat(), video_id, step.position),
            )
            previous = due
        return self.get(video_id)

    def delete(self, video_id: int) -> bool:
        return self.db.execute("DELETE FROM videos WHERE id = ?", (video_id,)).rowcount > 0

    def published_between(self, start: date, end: date) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM videos WHERE published_on BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
        return row["n"]

    def scheduled_between(self, start: date, end: date) -> int:
        """Unpublished videos planned to go live between start and end."""
        return sum(1 for v in self.active() if start <= v.publish_date <= end)


class VideosModule(Module):
    name = "videos"

    def setup(self) -> None:
        self.core.db.migrate(self.name, SCHEMA)
        self.service = VideoService(self.core.db)

    def menu_buttons(self) -> list[MenuButton]:
        return [
            MenuButton("🎬 סרטון חדש", self.start_new, order=10),
            MenuButton("📅 הסרטונים שלי", self.show_list, order=11),
        ]

    def commands(self) -> list[Command]:
        return [
            Command("video", "תכנון סרטון חדש", self.start_new),
            Command("videos", "הסרטונים שבתהליך", self.show_list),
        ]

    # -- creating a video ---------------------------------------------------------
    async def start_new(self, update: Update, context: Ctx) -> None:
        await self.ask(update, context, "title", "🎬 <b>סרטון חדש!</b>\nמה השם או הנושא של הסרטון?")

    async def ask_publish_date(self, update: Update, context: Ctx, title: str, idea_id: int | None = None) -> None:
        buttons = InlineKeyboardMarkup(
            [
                [self.button("מחר", "date", 1), self.button("בעוד 3 ימים", "date", 3)],
                [self.button("בעוד שבוע", "date", 7), self.button("בעוד שבועיים", "date", 14)],
            ]
        )
        prompt = f"📅 מתי לפרסם את <b>{ui.esc(title)}</b>?\nאפשר לבחור, או לכתוב: 25.10 / יום חמישי / בעוד 5 ימים"
        await self.ask(update, context, "date", prompt, {"title": title, "idea_id": idea_id}, buttons)

    async def on_input(self, update: Update, context: Ctx, step: str, data: dict[str, Any]) -> None:
        text = (update.effective_message.text or "").strip()
        if step == "title":
            await self.ask_publish_date(update, context, text[:100])
        elif step == "date":
            today = self.core.today()
            day = parse_date(text, today)
            if day is None or day < today:
                await ui.reply(update, "🤔 לא הצלחתי להבין את התאריך (או שהוא כבר עבר).")
                await self.ask_publish_date(update, context, data["title"], data.get("idea_id"))
                return
            await self._create(update, data, day)

    async def _create(self, update: Update, data: dict[str, Any], publish: date) -> None:
        today = self.core.today()
        video = self.service.create(data["title"], publish, today)
        lines = [f"✅ <b>{ui.esc(video.title)}</b> נכנס ללוח!", ""]
        lines += [f"{s.info.emoji} {s.info.name} – {ui.fmt_day(s.due, today)}" for s in video.steps]
        lines += ["", "אזכיר לך בכל שלב. יאללה! 💪"]
        await ui.reply(update, "\n".join(lines))
        await self.core.events.emit("video.created", video=video, idea_id=data.get("idea_id"))

    # -- list ---------------------------------------------------------------------
    def _list_view(self) -> tuple[str, InlineKeyboardMarkup | None]:
        today = self.core.today()
        videos = self.service.active()
        if not videos:
            return "📭 אין סרטונים בתהליך.\nלחץ על <b>🎬 סרטון חדש</b> כדי להתחיל!", None
        lines, rows = ["📅 <b>הסרטונים שלך</b>"], []
        for number, video in enumerate(videos[:10], start=1):
            current = video.current
            stages = []
            for step in video.steps:
                if step.done_on:
                    stages.append(f"✅ {step.info.name}")
                elif step is current:
                    late = " ⚠️" if step.due < today else ""
                    stages.append(f"👉 {step.info.name} ({ui.fmt_day(step.due, today)}){late}")
                else:
                    stages.append(f"⏳ {step.info.name}")
            lines += ["", f"<b>{number}. {ui.esc(video.title)}</b>", " · ".join(stages)]
            rows.append(
                [
                    self.button(f"✅ {number} · {current.info.name}", "done", video.id, current.key, "l"),
                    self.button(f"⏰ {number} · מחר", "snooze", video.id, current.key, "l"),
                    self.button(f"🗑️ {number}", "del", video.id),
                ]
            )
        if len(videos) > 10:
            lines.append(f"\n…ועוד {len(videos) - 10}")
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def show_list(self, update: Update, context: Ctx) -> None:
        text, markup = self._list_view()
        await ui.reply(update, text, markup)

    # -- buttons ------------------------------------------------------------------
    async def on_callback(self, update: Update, context: Ctx, action: str, args: list[str]) -> str | None:
        query = update.callback_query
        today = self.core.today()

        if action == "date":
            awaiting = self.core.pop_awaiting(context, self.name, "date")
            if awaiting is None:
                return "הבקשה פגה – אפשר להתחיל שוב 🙂"
            await ui.clear_buttons(query)
            await self._create(update, awaiting["data"], today + timedelta(days=int(args[0])))
            return None

        if action == "done":
            from_list = len(args) > 2
            result = self.service.complete_step(int(args[0]), args[1], today)
            await self._refresh(update, from_list)
            if result is None:
                return "כבר מסומן ✅"
            video, step, next_step = result
            await self.core.events.emit("video.step_done", video=video, step=step, next_step=next_step)
            if next_step is None:
                await self.core.events.emit("video.published", video=video)
            return f"✅ {step.info.name} – בוצע!"

        if action == "snooze":
            from_list = len(args) > 2
            video = self.service.snooze(int(args[0]), args[1], today)
            await self._refresh(update, from_list)
            return "⏰ נדחה למחר" if video else "כבר עודכן"

        if action == "del":
            video = self.service.get(int(args[0]))
            if video is None:
                return "הסרטון כבר לא קיים"
            confirm = InlineKeyboardMarkup(
                [[self.button("🗑️ כן, למחוק", "delok", video.id), self.button("↩️ לא", "keep")]]
            )
            await ui.reply(update, f"למחוק את <b>{ui.esc(video.title)}</b> מהלוח?", confirm)
            return None

        if action == "delok":
            deleted = self.service.delete(int(args[0]))
            await ui.edit_or_reply(update, "🗑️ נמחק מהלוח." if deleted else "הסרטון כבר לא קיים.")
            return None

        if action == "keep":
            await ui.edit_or_reply(update, "👍 נשאר בלוח.")
            return None
        return None

    async def _refresh(self, update: Update, from_list: bool) -> None:
        if from_list:
            text, markup = self._list_view()
            await ui.edit_or_reply(update, text, markup)
        else:
            await ui.drop_row(update.callback_query)

    # -- digest -------------------------------------------------------------------
    def _due_rows(self, due: list[tuple[Video, VideoStep]]) -> list[list[Any]]:
        return [
            [
                self.button(f"✅ {step.info.name} · {ui.short(video.title, 18)}", "done", video.id, step.key),
                self.button("⏰ מחר", "snooze", video.id, step.key),
            ]
            for video, step in due
        ]

    def _due_lines(self, due: list[tuple[Video, VideoStep]], day: date) -> list[str]:
        lines = []
        for video, step in due:
            late = (day - step.due).days
            tag = f" ⚠️ באיחור של {ui.days_text(late)}" if late > 0 else ""
            lines.append(f"• {step.info.emoji} {step.info.verb}: <b>{ui.esc(video.title)}</b>{tag}")
        return lines

    async def morning_section(self, day: date) -> DigestSection | None:
        due = self.service.due(day)
        upcoming = self.service.upcoming(day)
        if not due and not upcoming:
            return None
        lines = ["🎬 <b>סרטונים</b>", *self._due_lines(due, day)]
        if upcoming:
            soon = ", ".join(
                f"{s.info.emoji} {ui.esc(ui.short(v.title))} ({ui.fmt_day(s.due, day)})" for v, s in upcoming
            )
            lines.append(f"<i>בקרוב:</i> {soon}")
        return DigestSection("\n".join(lines), self._due_rows(due), open_count=len(due))

    async def evening_section(self, day: date) -> DigestSection | None:
        due = self.service.due(day)
        if not due:
            return None
        lines = ["🎬 <b>עדיין פתוח</b>", *self._due_lines(due, day)]
        return DigestSection("\n".join(lines), self._due_rows(due), open_count=len(due))
