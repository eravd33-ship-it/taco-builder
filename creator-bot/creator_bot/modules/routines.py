"""🔁 Routines: recurring channel chores (reply to comments, community post...).

Events emitted:
  routine.done(routine, day)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from telegram import InlineKeyboardMarkup, Update

from ..core import ui
from ..core.module import Command, Ctx, DigestSection, MenuButton, Module
from ..storage import Database

TEMPLATES = (
    "💬 להגיב לתגובות",
    "📊 לבדוק אנליטיקס",
    "📣 פוסט בקהילה",
    "📱 להעלות שורט",
    "🔍 לחפש רעיונות וטרנדים",
)

SCHEMA = [
    """
    CREATE TABLE routines (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        weekdays TEXT NOT NULL,
        created_on TEXT NOT NULL
    );
    CREATE TABLE routine_done (
        routine_id INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        PRIMARY KEY (routine_id, day)
    );
    """,
]


@dataclass
class Routine:
    id: int
    title: str
    weekdays: frozenset[int]  # Python weekday numbers (Monday=0)


class RoutineService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, title: str, weekdays: set[int], today: date) -> Routine:
        days = ",".join(str(d) for d in sorted(weekdays))
        cursor = self.db.execute(
            "INSERT INTO routines (title, weekdays, created_on) VALUES (?, ?, ?)", (title, days, today.isoformat())
        )
        return Routine(cursor.lastrowid, title, frozenset(weekdays))

    def all(self) -> list[Routine]:
        return [
            Routine(row["id"], row["title"], frozenset(int(d) for d in row["weekdays"].split(",") if d))
            for row in self.db.query("SELECT * FROM routines ORDER BY id")
        ]

    def get(self, routine_id: int) -> Routine | None:
        return next((r for r in self.all() if r.id == routine_id), None)

    def delete(self, routine_id: int) -> bool:
        return self.db.execute("DELETE FROM routines WHERE id = ?", (routine_id,)).rowcount > 0

    def due_on(self, day: date) -> list[Routine]:
        return [r for r in self.all() if day.weekday() in r.weekdays]

    def is_done(self, routine_id: int, day: date) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM routine_done WHERE routine_id = ? AND day = ?", (routine_id, day.isoformat())
        )
        return row is not None

    def mark_done(self, routine_id: int, day: date) -> bool:
        """Returns True only the first time, so a double tap is harmless."""
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO routine_done (routine_id, day) VALUES (?, ?)", (routine_id, day.isoformat())
        )
        return cursor.rowcount > 0

    def open_on(self, day: date) -> list[Routine]:
        return [r for r in self.due_on(day) if not self.is_done(r.id, day)]


class RoutinesModule(Module):
    name = "routines"

    def setup(self) -> None:
        self.core.db.migrate(self.name, SCHEMA)
        self.service = RoutineService(self.core.db)

    def menu_buttons(self) -> list[MenuButton]:
        return [MenuButton("🔁 משימות קבועות", self.show_list, order=20)]

    def commands(self) -> list[Command]:
        return [Command("routines", "משימות קבועות", self.show_list)]

    @property
    def week_order(self) -> list[int]:
        first = self.core.settings.week_start
        return [(first + i) % 7 for i in range(7)]

    def fmt_days(self, weekdays: frozenset[int] | set[int]) -> str:
        if len(weekdays) == 7:
            return "כל יום"
        return ", ".join(ui.WEEKDAY_LETTERS[d] for d in self.week_order if d in weekdays)

    # -- list ---------------------------------------------------------------------
    def _list_view(self) -> tuple[str, InlineKeyboardMarkup]:
        today = self.core.today()
        routines = self.service.all()
        rows = [[self.button(f"🗑️ {ui.short(r.title)}", "del", r.id)] for r in routines]
        rows.append([self.button("➕ משימה קבועה חדשה", "new")])
        if not routines:
            text = "🔁 <b>משימות קבועות</b>\n\nעוד אין. משימות שחוזרות כל שבוע – ואני אזכיר לך אותן בבוקר."
            return text, InlineKeyboardMarkup(rows)
        lines = ["🔁 <b>משימות קבועות</b>", ""]
        for r in routines:
            status = " ✅" if today.weekday() in r.weekdays and self.service.is_done(r.id, today) else ""
            lines.append(f"• {ui.esc(r.title)} – {self.fmt_days(r.weekdays)}{status}")
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def show_list(self, update: Update, context: Ctx) -> None:
        text, markup = self._list_view()
        await ui.reply(update, text, markup)

    # -- adding -------------------------------------------------------------------
    def _days_keyboard(self, selected: set[int]) -> InlineKeyboardMarkup:
        day_buttons = [
            self.button(("✔️ " if d in selected else "") + ui.WEEKDAY_LETTERS[d], "day", d) for d in self.week_order
        ]
        return InlineKeyboardMarkup(
            [
                day_buttons[:4],
                day_buttons[4:],
                [self.button("כל יום", "all"), self.button("💾 שמירה", "save")],
            ]
        )

    async def _ask_days(self, update: Update, context: Ctx, title: str) -> None:
        context.user_data["routine_draft"] = {"title": title, "days": []}
        await ui.reply(update, f"📆 באילו ימים <b>{ui.esc(title)}</b>?", self._days_keyboard(set()))

    async def on_input(self, update: Update, context: Ctx, step: str, data: dict[str, Any]) -> None:
        if step == "title":
            await self._ask_days(update, context, (update.effective_message.text or "").strip()[:80])

    async def on_callback(self, update: Update, context: Ctx, action: str, args: list[str]) -> str | None:
        query = update.callback_query
        today = self.core.today()

        if action == "new":
            rows = [[self.button(t, "tpl", i)] for i, t in enumerate(TEMPLATES)]
            rows.append([self.button("✍️ משהו אחר", "custom")])
            await ui.reply(update, "➕ איזו משימה קבועה?", InlineKeyboardMarkup(rows))
            return None
        if action == "tpl":
            await ui.edit_or_reply(update, f"➕ {TEMPLATES[int(args[0])]}")
            await self._ask_days(update, context, TEMPLATES[int(args[0])])
            return None
        if action == "custom":
            await self.ask(update, context, "title", "✍️ מה המשימה?")
            return None

        if action in ("day", "all", "save"):
            draft = context.user_data.get("routine_draft")
            if not draft:
                return "הבקשה פגה – אפשר להתחיל שוב 🙂"
            days = set(draft["days"])
            if action == "day":
                days ^= {int(args[0])}
            elif action == "all":
                days = set() if len(days) == 7 else set(range(7))
            else:
                if not days:
                    return "צריך לבחור לפחות יום אחד"
                routine = self.service.add(draft["title"], days, today)
                context.user_data.pop("routine_draft", None)
                await ui.edit_or_reply(
                    update,
                    f"✅ נשמר: <b>{ui.esc(routine.title)}</b> – {self.fmt_days(routine.weekdays)}\nאזכיר לך בבוקר 🔔",
                )
                return None
            draft["days"] = sorted(days)
            await query.edit_message_reply_markup(self._days_keyboard(days))
            return None

        if action == "done":
            routine = self.service.get(int(args[0]))
            await ui.drop_row(query)
            if routine is None:
                return "המשימה כבר לא קיימת"
            if self.service.mark_done(routine.id, today):
                await self.core.events.emit("routine.done", routine=routine, day=today)
            return "✅ כל הכבוד!"

        if action == "del":
            self.service.delete(int(args[0]))
            text, markup = self._list_view()
            await ui.edit_or_reply(update, text, markup)
            return "🗑️ נמחק"
        return None

    # -- digest -------------------------------------------------------------------
    def _section(self, title: str, routines: list[Routine]) -> DigestSection:
        lines = [title, *(f"• {ui.esc(r.title)}" for r in routines)]
        rows = [[self.button(f"✅ {ui.short(r.title, 28)}", "done", r.id)] for r in routines]
        return DigestSection("\n".join(lines), rows, open_count=len(routines))

    async def morning_section(self, day: date) -> DigestSection | None:
        routines = self.service.open_on(day)
        return self._section("🔁 <b>משימות קבועות להיום</b>", routines) if routines else None

    async def evening_section(self, day: date) -> DigestSection | None:
        routines = self.service.open_on(day)
        return self._section("🔁 <b>משימות קבועות שנשארו</b>", routines) if routines else None
