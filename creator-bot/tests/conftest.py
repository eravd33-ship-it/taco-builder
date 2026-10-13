from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from telegram.request import BaseRequest

from creator_bot.config import Settings
from creator_bot.core.app import Core
from creator_bot.storage import Database

TZ = ZoneInfo("Asia/Jerusalem")
OWNER = 42
TODAY = date(2026, 10, 13)  # a Tuesday; with Sunday-first weeks, the week is 11.10–17.10


class Clock:
    def __init__(self, day: date) -> None:
        self.set(day)

    def set(self, day: date) -> None:
        self.now = datetime.combine(day, time(9, 0), TZ)

    def advance(self, days: int) -> None:
        self.now += timedelta(days=days)

    def __call__(self) -> datetime:
        return self.now


class FakeBot:
    """Stands in for Telegram in unit tests: records what would be sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None, **_: Any) -> None:
        self.sent.append(text)


class FakeRequest(BaseRequest):
    """Answers Bot API calls locally, so the whole bot can run in tests without network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @property
    def read_timeout(self) -> float:
        return 1.0

    async def do_request(self, url: str, method: str, request_data: Any = None, **_: Any) -> tuple[int, bytes]:
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == "getMe":
            result: Any = {"id": 1, "is_bot": True, "first_name": "Bot", "username": "creator_test_bot"}
        elif endpoint in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
            result = {
                "message_id": len(self.calls),
                "date": 0,
                "chat": {"id": params.get("chat_id", OWNER), "type": "private"},
                "text": params.get("text", ""),
            }
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent(self, endpoint: str = "sendMessage") -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == endpoint]

    def last_text(self) -> str:
        return self.sent()[-1]["text"]

    def buttons(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        markup = params.get("reply_markup") or {}
        return [button for row in markup.get("inline_keyboard", []) for button in row]


@pytest.fixture
def clock() -> Clock:
    return Clock(TODAY)


@pytest.fixture
def make_core(clock: Clock):
    def factory(modules: list, **settings: Any) -> Core:
        core = Core(Settings(telegram_token="1:x", owner_chat_id=OWNER, **settings), Database(), clock)
        core.bot = FakeBot()
        core.load(modules)
        return core

    return factory
