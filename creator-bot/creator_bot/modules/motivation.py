"""💪 Motivation: daily quote, upload/views goals, streaks, praise and gentle nudges.

Listens to: video.created, video.step_done, video.published, routine.done,
channel.milestone.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from telegram import InlineKeyboardMarkup, Update

from ..core import ui
from ..core.dates import month_start, week_start
from ..core.module import Command, Ctx, DigestSection, MenuButton, Module
from . import motivation_texts as texts

UPLOADS_GOAL = "motivation.uploads_per_week"
VIEWS_GOAL = "motivation.views_per_month"
LAST_ACTIVITY = "motivation.last_activity"


@dataclass
class WeekProgress:
    start: date
    end: date
    done: int
    goal: int
    scheduled: int  # unpublished videos planned for the rest of the week

    @property
    def met(self) -> bool:
        return self.done >= self.goal


def parse_number(text: str) -> int | None:
    """Understand "10000", "10,000", "10k", "2.5K", "10 אלף"."""
    value = text.strip().lower().replace(",", "").replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(k|אלף)?", value)
    if not match:
        return None
    number = float(match.group(1)) * (1000 if match.group(2) else 1)
    return int(number)


class MotivationModule(Module):
    name = "motivation"

    def setup(self) -> None:
        events = self.core.events
        events.on("video.created", self._on_activity)
        events.on("routine.done", self._on_activity)
        events.on("video.step_done", self._on_step_done)
        events.on("video.published", self._on_published)
        events.on("channel.milestone", self._on_milestone)

    def menu_buttons(self) -> list[MenuButton]:
        return [
            MenuButton("🎯 יעדים", self.show_goals, order=40),
            MenuButton("💪 מוטיבציה", self.boost, order=41),
        ]

    def commands(self) -> list[Command]:
        return [
            Command("goals", "היעדים וההתקדמות שלך", self.show_goals),
            Command("motivate", "זריקת מוטיבציה", self.boost),
        ]

    # -- goals & progress ---------------------------------------------------------
    @property
    def uploads_goal(self) -> int:
        return self.core.db.get(UPLOADS_GOAL, self.core.settings.uploads_per_week)

    @property
    def views_goal(self) -> int | None:
        return self.core.db.get(VIEWS_GOAL)

    @property
    def videos(self) -> Any:
        return self.core.get("videos")

    def week_progress(self, day: date) -> WeekProgress | None:
        if self.videos is None:
            return None
        start = week_start(day, self.core.settings.week_start)
        end = start + timedelta(days=6)
        service = self.videos.service
        return WeekProgress(
            start, end, service.published_between(start, day), self.uploads_goal, service.scheduled_between(day, end)
        )

    def streak(self, day: date) -> int:
        """Weeks in a row the upload goal was met (this week counts once it is met)."""
        if self.videos is None or self.uploads_goal <= 0:
            return 0
        service, goal = self.videos.service, self.uploads_goal
        start = week_start(day, self.core.settings.week_start)
        count = 1 if service.published_between(start, day) >= goal else 0
        start -= timedelta(days=7)
        while count < 520 and service.published_between(start, start + timedelta(days=6)) >= goal:
            count += 1
            start -= timedelta(days=7)
        return count

    def views_progress(self, day: date) -> tuple[int, int] | None:
        stats = self.core.get("youtube_stats")
        goal = self.views_goal
        if stats is None or not goal:
            return None
        gained = stats.service.views_between(month_start(day), day)
        return (gained, goal) if gained is not None else None

    def quote_for(self, day: date) -> str:
        return texts.QUOTES[day.toordinal() % len(texts.QUOTES)]

    def _week_lines(self, day: date) -> list[str]:
        progress = self.week_progress(day)
        if progress is None:
            return []
        lines = [f"🚀 השבוע: <b>{progress.done}/{progress.goal}</b> העלאות"]
        if progress.met:
            lines.append(random.choice(texts.GOAL_MET))
            return lines
        missing = progress.goal - progress.done - progress.scheduled
        if missing <= 0:
            planned = ui.count_text(progress.scheduled, "סרטון אחד מתוכנן", "סרטונים מתוכננים")
            lines.append(f"👌 בדרך הנכונה – {planned} עד סוף השבוע.")
        else:
            days_left = (progress.end - day).days + 1
            need = "חסר עוד סרטון אחד" if missing == 1 else f"חסרים עוד {missing} סרטונים"
            lines.append(f"⏳ {need} ליעד, ונשארו {ui.days_text(days_left)}. אולי להפוך רעיון לסרטון? 💡")
        return lines

    def _streak_line(self, day: date) -> str | None:
        streak = self.streak(day)
        if streak >= 2:
            return f"🔥 רצף: <b>{streak}</b> שבועות ברצף ביעד!"
        return None

    # -- record activity & celebrate ---------------------------------------------
    async def _on_activity(self, **_: Any) -> None:
        self.core.db.set(LAST_ACTIVITY, self.core.today().isoformat())

    async def _on_step_done(self, video: Any, step: Any, next_step: Any, **_: Any) -> None:
        await self._on_activity()
        if next_step is None:
            return  # the video was published – _on_published celebrates
        praise = random.choice(texts.STEP_PRAISE.get(step.key, texts.STEP_PRAISE_DEFAULT))
        when = ui.fmt_day(next_step.due, self.core.today())
        await self.core.notify(
            f"{praise}\nהבא בתור: {next_step.info.emoji} {next_step.info.name} של <b>{ui.esc(video.title)}</b> – {when}"
        )

    async def _on_published(self, video: Any, **_: Any) -> None:
        day = self.core.today()
        lines = [f"🎉🎉🎉 <b>{ui.esc(video.title)}</b> באוויר!", random.choice(texts.PUBLISHED), ""]
        progress = self.week_progress(day)
        if progress:
            lines.append(f"🚀 השבוע: {progress.done}/{progress.goal}  {ui.progress_bar(progress.done, progress.goal)}")
            if progress.met:
                lines.append(random.choice(texts.GOAL_MET))
            else:
                left = progress.goal - progress.done
                lines.append(f"עוד {ui.count_text(left, 'סרטון אחד', 'סרטונים')} ליעד השבועי 💪")
        if streak_line := self._streak_line(day):
            lines.append(streak_line)
        await self.core.notify("\n".join(lines))

    async def _on_milestone(self, metric: str, value: int, **_: Any) -> None:
        metric_he = {"subscribers": "מנויים", "views": "צפיות"}.get(metric, metric)
        await self.core.notify(random.choice(texts.MILESTONE).format(value=f"{value:,}", metric=metric_he))

    # -- digest -------------------------------------------------------------------
    async def morning_section(self, day: date) -> DigestSection | None:
        lines = [f"💬 <i>{self.quote_for(day)}</i>"]
        if day == week_start(day, self.core.settings.week_start) and self.videos is not None:
            last_start = day - timedelta(days=7)
            done = self.videos.service.published_between(last_start, day - timedelta(days=1))
            verdict = "✅ עמדת ביעד!" if done >= self.uploads_goal else "– שבוע חדש, הזדמנות חדשה!"
            lines.append(f"📊 <b>סיכום השבוע שעבר:</b> {done}/{self.uploads_goal} העלאות {verdict}")
        lines += self._week_lines(day)
        if views := self.views_progress(day):
            gained, goal = views
            lines.append(f"👀 החודש: <b>{gained:,}/{goal:,}</b> צפיות ({min(100, round(gained / goal * 100))}%)")
        if streak_line := self._streak_line(day):
            lines.append(streak_line)
        return DigestSection("\n".join(lines))

    async def digest_footer(self, kind: str, day: date, open_count: int) -> str | None:
        if kind == "morning":
            return random.choice(texts.QUIET_MORNING) if open_count == 0 else None
        if open_count > 0:
            tasks = ui.count_text(open_count, "משימה אחת", "משימות")
            return random.choice(texts.EVENING_OPEN).format(tasks=tasks)
        last = self.core.db.get(LAST_ACTIVITY)
        if last is None:
            return None
        idle_days = (day - date.fromisoformat(last)).days
        if idle_days == 0:
            return random.choice(texts.ALL_DONE)
        if idle_days >= 3:
            return random.choice(texts.INACTIVE).format(days=ui.days_text(idle_days))
        return None

    # -- screens ------------------------------------------------------------------
    def _goals_view(self) -> tuple[str, InlineKeyboardMarkup]:
        day = self.core.today()
        lines = ["🎯 <b>היעדים שלך</b>", ""]
        progress = self.week_progress(day)
        if progress:
            lines += [
                f"🚀 העלאות השבוע: <b>{progress.done}/{progress.goal}</b>",
                ui.progress_bar(progress.done, progress.goal),
            ]
        stats_enabled = self.core.get("youtube_stats") is not None
        if stats_enabled:
            views = self.views_progress(day)
            if views:
                gained, goal = views
                lines += [f"👀 צפיות החודש: <b>{gained:,}/{goal:,}</b>", ui.progress_bar(gained, goal)]
            elif self.views_goal:
                lines.append(f"👀 יעד צפיות חודשי: {self.views_goal:,} (הנתונים יופיעו אחרי יום-יומיים של מעקב)")
            else:
                lines.append("👀 עוד לא הוגדר יעד צפיות חודשי")
        lines.append(f"🔥 רצף: <b>{self.streak(day)}</b> שבועות ברצף ביעד ההעלאות")
        row = [self.button("✏️ יעד העלאות", "goalup")]
        if stats_enabled:
            row.append(self.button("✏️ יעד צפיות", "goalviews"))
        return "\n".join(lines), InlineKeyboardMarkup([row])

    async def show_goals(self, update: Update, context: Ctx) -> None:
        text, markup = self._goals_view()
        await ui.reply(update, text, markup)

    async def boost(self, update: Update, context: Ctx) -> None:
        lines = [f"💪 <i>{random.choice(texts.QUOTES)}</i>", ""]
        day = self.core.today()
        progress = self.week_progress(day)
        if progress:
            lines.append(f"השבוע: {progress.done}/{progress.goal} העלאות · רצף {self.streak(day)} 🔥")
            if not progress.met and self.videos.service.due(day):
                lines.append("יש משימה שמחכה לך היום – בוא נסגור אותה! 👉 📋 היום")
        await ui.reply(update, "\n".join(lines))

    async def on_callback(self, update: Update, context: Ctx, action: str, args: list[str]) -> str | None:
        if action == "goalup":
            numbers = [self.button(str(n), "setup", n) for n in range(1, 8)]
            await ui.reply(update, "🚀 כמה סרטונים בשבוע?", InlineKeyboardMarkup([numbers[:4], numbers[4:]]))
            return None
        if action == "setup":
            self.core.db.set(UPLOADS_GOAL, int(args[0]))
            await ui.edit_or_reply(
                update, f"✅ היעד עודכן: {ui.count_text(int(args[0]), 'סרטון אחד', 'סרטונים')} בשבוע. יאללה! 💪"
            )
            return None
        if action == "goalviews":
            await self.ask(update, context, "views", "👀 כמה צפיות בחודש? (למשל 10000 או 10k, 0 = בלי יעד)")
            return None
        return None

    async def on_input(self, update: Update, context: Ctx, step: str, data: dict[str, Any]) -> None:
        if step != "views":
            return
        number = parse_number(update.effective_message.text or "")
        if number is None:
            await self.ask(update, context, "views", "🤔 צריך מספר, למשל 10000 או 10k:")
            return
        self.core.db.set(VIEWS_GOAL, number or None)
        text = f"✅ יעד הצפיות החודשי: {number:,}. בוא נשבור אותו! 🚀" if number else "👍 יעד הצפיות הוסר."
        await ui.reply(update, text)
