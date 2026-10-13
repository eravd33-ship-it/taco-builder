"""💡 Ideas: capture video ideas in a second and turn them into scheduled videos.

Listens to: video.created – marks the idea as used when a video was made from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from telegram import InlineKeyboardMarkup, Update

from ..core import ui
from ..core.module import Command, Ctx, DigestSection, MenuButton, Module
from ..storage import Database

SCHEMA = [
    """
    CREATE TABLE ideas (
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        created_on TEXT NOT NULL,
        video_id INTEGER
    );
    """,
]


@dataclass
class Idea:
    id: int
    text: str


class IdeaService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, text: str, today: date) -> Idea:
        cursor = self.db.execute("INSERT INTO ideas (text, created_on) VALUES (?, ?)", (text, today.isoformat()))
        return Idea(cursor.lastrowid, text)

    def open(self, limit: int = 100) -> list[Idea]:
        rows = self.db.query("SELECT id, text FROM ideas WHERE video_id IS NULL ORDER BY id DESC LIMIT ?", (limit,))
        return [Idea(row["id"], row["text"]) for row in rows]

    def count_open(self) -> int:
        return self.db.query_one("SELECT COUNT(*) AS n FROM ideas WHERE video_id IS NULL")["n"]

    def get(self, idea_id: int) -> Idea | None:
        row = self.db.query_one("SELECT id, text FROM ideas WHERE id = ?", (idea_id,))
        return Idea(row["id"], row["text"]) if row else None

    def delete(self, idea_id: int) -> bool:
        return self.db.execute("DELETE FROM ideas WHERE id = ?", (idea_id,)).rowcount > 0

    def mark_used(self, idea_id: int, video_id: int) -> None:
        self.db.execute("UPDATE ideas SET video_id = ? WHERE id = ?", (video_id, idea_id))


class IdeasModule(Module):
    name = "ideas"

    def setup(self) -> None:
        self.core.db.migrate(self.name, SCHEMA)
        self.service = IdeaService(self.core.db)
        self.core.events.on("video.created", self._on_video_created)

    def menu_buttons(self) -> list[MenuButton]:
        return [MenuButton("💡 רעיונות", self.show_list, order=30)]

    def commands(self) -> list[Command]:
        return [
            Command("idea", "שמירת רעיון: /idea הטקסט", self.cmd_idea),
            Command("ideas", "רשימת הרעיונות", self.show_list),
        ]

    async def _on_video_created(self, video: Any, idea_id: int | None = None, **_: Any) -> None:
        if idea_id:
            self.service.mark_used(idea_id, video.id)

    async def _save(self, update: Update, text: str) -> None:
        idea = self.service.add(text[:300], self.core.today())
        count = self.service.count_open()
        await ui.reply(
            update,
            f"💡 נשמר! יש לך עכשיו {ui.count_text(count, 'רעיון אחד', 'רעיונות')} ברשימה.",
            InlineKeyboardMarkup([[self.button("🎬 להפוך לסרטון עכשיו", "video", idea.id)]]),
        )

    async def cmd_idea(self, update: Update, context: Ctx) -> None:
        text = " ".join(context.args or []).strip()
        if text:
            await self._save(update, text)
        else:
            await self.ask(update, context, "text", "💡 מה הרעיון?")

    async def on_input(self, update: Update, context: Ctx, step: str, data: dict[str, Any]) -> None:
        text = (update.effective_message.text or "").strip()
        if step == "text" and text:
            await self._save(update, text)

    async def on_free_text(self, update: Update, context: Ctx) -> bool:
        context.user_data["idea_candidate"] = (update.effective_message.text or "").strip()[:300]
        await ui.reply(
            update,
            "🤔 לא בטוח מה לעשות עם זה. לשמור כרעיון לסרטון?\n<i>(או לבחור פעולה מהתפריט למטה)</i>",
            InlineKeyboardMarkup([[self.button("💡 כן, לשמור כרעיון", "savefree")]]),
        )
        return True

    def _list_view(self) -> tuple[str, InlineKeyboardMarkup]:
        ideas = self.service.open(limit=10)
        rows = [
            [self.button(f"🎬 {ui.short(idea.text, 26)}", "video", idea.id), self.button("🗑️", "del", idea.id)]
            for idea in ideas
        ]
        rows.append([self.button("➕ רעיון חדש", "new")])
        if not ideas:
            return (
                "💡 <b>רעיונות</b>\n\nהרשימה ריקה. כל רעיון שעולה – לשלוח לי, ואשמור אותו כאן.",
                InlineKeyboardMarkup(rows),
            )
        total = self.service.count_open()
        lines = [f"💡 <b>הרעיונות שלך</b> ({total})", ""]
        lines += [f"{number}. {ui.esc(idea.text)}" for number, idea in enumerate(ideas, start=1)]
        lines += ["", "🎬 = להפוך לסרטון מתוזמן"]
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    async def show_list(self, update: Update, context: Ctx) -> None:
        text, markup = self._list_view()
        await ui.reply(update, text, markup)

    async def on_callback(self, update: Update, context: Ctx, action: str, args: list[str]) -> str | None:
        if action == "new":
            await self.ask(update, context, "text", "💡 מה הרעיון?")
            return None
        if action == "list":
            await self.show_list(update, context)
            return None
        if action == "savefree":
            text = context.user_data.pop("idea_candidate", "")
            if not text:
                return "הבקשה פגה"
            await ui.drop_row(update.callback_query)
            await self._save(update, text)
            return None
        if action == "del":
            self.service.delete(int(args[0]))
            text, markup = self._list_view()
            await ui.edit_or_reply(update, text, markup)
            return "🗑️ נמחק"
        if action == "video":
            videos = self.core.get("videos")
            idea = self.service.get(int(args[0]))
            if videos is None or idea is None:
                return "לא זמין"
            await videos.ask_publish_date(update, context, idea.text[:100], idea_id=idea.id)
            return None
        return None

    async def morning_section(self, day: date) -> DigestSection | None:
        """Nothing in production? Nudge toward turning an idea into a video."""
        videos = self.core.get("videos")
        if videos is None or videos.service.active():
            return None
        count = self.service.count_open()
        if count == 0:
            return None
        text = (
            f"💡 אין סרטון בתהליך, אבל מחכים לך {ui.count_text(count, 'רעיון אחד', 'רעיונות')}. "
            "מה דעתך להפוך אחד לסרטון היום?"
        )
        return DigestSection(text, [[self.button("💡 לרשימת הרעיונות", "list")]])
