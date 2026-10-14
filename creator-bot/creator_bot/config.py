"""Settings, read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def load_dotenv(path: Path) -> None:
    """Minimal .env loader, so no extra dependency is needed. Real env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_time(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


@dataclass
class Settings:
    telegram_token: str
    owner_chat_id: int | None = None
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Jerusalem"))
    morning_time: time = time(9, 0)
    evening_time: time = time(20, 30)
    week_start: int = WEEKDAYS["sunday"]  # Python weekday number (Monday=0)
    uploads_per_week: int = 1
    db_path: Path = Path("data/creator_bot.db")
    disabled_modules: frozenset[str] = frozenset()
    # YouTube channel statistics (optional)
    youtube_api_key: str | None = None
    youtube_channel: str | None = None  # channel id (UC...) or @handle
    # Google Drive (optional)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_refresh_token: str | None = None
    google_scopes: frozenset[str] = frozenset()  # granted scopes; empty = unknown (e.g. set through .env)

    @classmethod
    def from_env(cls, require_token: bool = True) -> Settings:
        env = os.environ.get
        token = env("TELEGRAM_BOT_TOKEN", "").strip()
        if not token and require_token:
            raise SystemExit("חסר TELEGRAM_BOT_TOKEN – העתק את .env.example ל-.env ומלא אותו (ראה README).")
        owner = env("OWNER_CHAT_ID", "").strip()
        week_start = env("WEEK_START", "sunday").strip().lower()
        if week_start not in WEEKDAYS:
            raise SystemExit(f"WEEK_START לא תקין: {week_start}")
        disabled = {name.strip() for name in env("DISABLED_MODULES", "").split(",") if name.strip()}
        return cls(
            telegram_token=token,
            owner_chat_id=int(owner) if owner else None,
            timezone=ZoneInfo(env("TIMEZONE", "Asia/Jerusalem")),
            morning_time=parse_time(env("MORNING_TIME", "09:00")),
            evening_time=parse_time(env("EVENING_TIME", "20:30")),
            week_start=WEEKDAYS[week_start],
            uploads_per_week=int(env("UPLOADS_PER_WEEK", "1")),
            db_path=Path(env("DB_PATH", "data/creator_bot.db")),
            disabled_modules=frozenset(disabled),
            youtube_api_key=env("YOUTUBE_API_KEY") or None,
            youtube_channel=env("YOUTUBE_CHANNEL") or None,
            google_client_id=env("GOOGLE_CLIENT_ID") or None,
            google_client_secret=env("GOOGLE_CLIENT_SECRET") or None,
            google_refresh_token=env("GOOGLE_REFRESH_TOKEN") or None,
        )
