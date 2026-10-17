"""The base class every feature extends.

A module is one self-contained feature (videos, ideas, motivation...). To add a
feature: subclass `Module`, implement only the hooks you need, and add the class
to `creator_bot/modules/__init__.py`. The core wires it into Telegram, the main
menu, the daily digest and the scheduler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, JobQueue

if TYPE_CHECKING:
    from ..config import Settings
    from .app import Core

Ctx = ContextTypes.DEFAULT_TYPE
Handler = Callable[[Update, Ctx], Awaitable[None]]


@dataclass
class MenuButton:
    """A button on the main menu keyboard. Lower `order` comes first."""

    text: str
    handler: Handler
    order: int = 100


@dataclass
class Command:
    """A slash command, e.g. /video. Shown in Telegram's command list."""

    name: str
    description: str
    handler: Handler


@dataclass
class DigestSection:
    """A module's part of the morning digest / evening check-in."""

    text: str
    buttons: list[list[InlineKeyboardButton]] = field(default_factory=list)
    open_count: int = 0  # how many tasks in this section still need doing


class Module:
    name: str = ""  # unique id: callback-data prefix and DB namespace

    def __init__(self, core: Core) -> None:
        self.core = core

    @classmethod
    def is_enabled(cls, settings: Settings) -> bool:
        """Return False when the module is missing configuration (e.g. API keys)."""
        return True

    # -- wiring -------------------------------------------------------------
    def setup(self) -> None:
        """Create tables (self.core.db.migrate) and subscribe to events."""

    def teardown(self) -> None:
        """Undo what setup() subscribed to, when the module is unloaded at runtime."""

    def menu_buttons(self) -> list[MenuButton]:
        return []

    def commands(self) -> list[Command]:
        return []

    def schedule(self, job_queue: JobQueue) -> None:
        """Register the module's own recurring jobs."""

    # -- interaction ----------------------------------------------------------
    async def on_callback(self, update: Update, context: Ctx, action: str, args: list[str]) -> str | None:
        """Handle an inline button made with `self.button()`. Return a short toast text."""
        return None

    async def on_input(self, update: Update, context: Ctx, step: str, data: dict[str, Any]) -> None:
        """Handle the user's answer to a question asked with `self.ask()`."""

    async def on_free_text(self, update: Update, context: Ctx) -> bool:
        """Text that no one asked for. Return True if the module handled it."""
        return False

    # -- daily digest -----------------------------------------------------------
    async def morning_section(self, day: date) -> DigestSection | None:
        return None

    async def evening_section(self, day: date) -> DigestSection | None:
        """Only things that are still open tonight."""
        return None

    async def digest_footer(self, kind: str, day: date, open_count: int) -> str | None:
        """A closing line for the digest ("morning"/"evening"). The first module to answer wins."""
        return None

    # -- helpers ------------------------------------------------------------------
    def cb(self, action: str, *args: object) -> str:
        data = ":".join([self.name, action, *(str(arg) for arg in args)])
        if len(data.encode()) > 64:  # Telegram's limit for callback data
            raise ValueError(f"callback data too long: {data}")
        return data

    def button(self, text: str, action: str, *args: object) -> InlineKeyboardButton:
        return InlineKeyboardButton(text, callback_data=self.cb(action, *args))

    async def ask(
        self,
        update: Update,
        context: Ctx,
        step: str,
        prompt: str,
        data: dict[str, Any] | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Ask a question; the next text message goes to `on_input(step, data)`."""
        await self.core.ask(update, context, self.name, step, prompt, data, reply_markup)
