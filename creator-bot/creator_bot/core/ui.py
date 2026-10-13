"""Formatting and messaging helpers shared by all modules."""

from __future__ import annotations

import html
from datetime import date

from telegram import CallbackQuery, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest

# Indexed by date.weekday() (Monday=0)
WEEKDAY_LETTERS = ("ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳", "א׳")


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def short(text: str, limit: int = 22) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_day(day: date, today: date | None = None) -> str:
    if today is not None:
        delta = (day - today).days
        if delta == 0:
            return "היום"
        if delta == 1:
            return "מחר"
    return f"יום {WEEKDAY_LETTERS[day.weekday()]} {day.day}.{day.month}"


def days_text(days: int) -> str:
    if days == 1:
        return "יום אחד"
    if days == 2:
        return "יומיים"
    return f"{days} ימים"


def count_text(count: int, one: str, many: str) -> str:
    """count_text(1, "משימה אחת", "משימות") -> "משימה אחת"; (3, ...) -> "3 משימות"."""
    return one if count == 1 else f"{count} {many}"


def progress_bar(value: int, goal: int, width: int = 10) -> str:
    ratio = min(value / goal, 1.0) if goal > 0 else 1.0
    filled = round(ratio * width)
    return "▓" * filled + "░" * (width - filled) + f" {round(ratio * 100)}%"


async def reply(
    update: Update,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> None:
    chat = update.effective_chat
    if chat is not None:
        await chat.send_message(text, reply_markup=reply_markup)


async def drop_row(query: CallbackQuery) -> None:
    """Remove the keyboard row of the pressed button (e.g. after marking a task done)."""
    markup = getattr(query.message, "reply_markup", None)
    if markup is None:
        return
    rows = [row for row in markup.inline_keyboard if not any(b.callback_data == query.data for b in row)]
    try:
        await query.edit_message_reply_markup(InlineKeyboardMarkup(rows) if rows else None)
    except BadRequest:
        pass  # message too old or unchanged – not worth failing the action


async def clear_buttons(query: CallbackQuery) -> None:
    try:
        await query.edit_message_reply_markup(None)
    except BadRequest:
        pass


async def edit_or_reply(update: Update, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the message the button sits on; fall back to a new message."""
    query = update.callback_query
    if query is not None:
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
            return
        except BadRequest as error:
            if "not modified" in str(error).lower():
                return
    await reply(update, text, reply_markup)
