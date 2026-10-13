"""Where the desktop app keeps its data, per operating system."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_FOLDER = "CreatorDesk"


def data_dir(platform: str | None = None, home: Path | None = None) -> Path:
    """The per-user folder for the database and an optional `.env` file."""
    platform = platform or sys.platform
    home = home or Path.home()
    if platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / APP_FOLDER


def ui_dir() -> Path:
    """The folder with index.html / app.js / app.css (works from source and when packaged)."""
    return Path(__file__).resolve().parent / "ui"
