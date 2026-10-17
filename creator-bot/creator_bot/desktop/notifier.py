"""Notifications: an in-app inbox plus native OS pop-ups.

The shared modules (motivation, Drive...) call `core.notify(html_text)`. On the
desktop `core.bot` is a `DesktopBot`, which turns that text into an inbox entry
and, when the window is not in front, an OS notification.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

APP_NAME = "Creator Desk"
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'<a href="([^"]+)"')
_BAR = re.compile(r"\s*[▓░]+\s*\d+%")  # the text progress bar made for Telegram
# A registered app id, so Windows actually shows the toast (the PowerShell one is always registered).
_WINDOWS_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
_WINDOWS_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] | Out-Null;"
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$n = $t.GetElementsByTagName('text');"
    "$n.Item(0).AppendChild($t.CreateTextNode($env:CD_TITLE)) | Out-Null;"
    "$n.Item(1).AppendChild($t.CreateTextNode($env:CD_BODY)) | Out-Null;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CD_APP_ID)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)


def to_plain(text: str) -> tuple[str, str | None]:
    """The module texts use Telegram's HTML subset: return (plain text, first link)."""
    link = _HREF.search(text)
    plain = html.unescape(_TAG.sub("", _BAR.sub("", text))).strip()
    return plain, html.unescape(link.group(1)) if link else None


def split_title(plain: str, title_limit: int = 70, body_limit: int = 240) -> tuple[str, str]:
    first, _, rest = plain.partition("\n")
    title = first.strip()[:title_limit] or APP_NAME
    body = " ".join(part.strip() for part in rest.splitlines() if part.strip())
    return title, body[:body_limit]


def notification_command(
    title: str, body: str, platform: str | None = None, which: Callable[[str], str | None] = shutil.which
) -> tuple[list[str], dict[str, str]] | None:
    """The command (and extra environment) that shows an OS notification, or None if unsupported."""
    platform = platform or sys.platform
    if platform == "darwin":
        script = f'display notification "{_applescript(body)}" with title "{_applescript(title)}"'
        return ["osascript", "-e", script], {}
    if platform == "win32":
        env = {"CD_TITLE": title, "CD_BODY": body, "CD_APP_ID": _WINDOWS_APP_ID}
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT], env
    if which("notify-send"):
        return ["notify-send", "--app-name", APP_NAME, title, body], {}
    return None


def _applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def show_os_notification(title: str, body: str) -> bool:
    """Blocking; call through a thread. Never raises: a missing notifier must not break the app."""
    command = notification_command(title, body)
    if command is None:
        return False
    args, extra_env = command
    try:
        subprocess.run(
            args,
            env={**os.environ, **extra_env},
            check=True,
            timeout=15,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except (OSError, subprocess.SubprocessError):
        log.warning("Could not show an OS notification", exc_info=True)
        return False


@dataclass
class FeedItem:
    id: int
    time: str
    text: str
    link: str | None
    read: bool = False


class Feed:
    """The in-app inbox (newest last). Kept in memory; it is a convenience, not a record."""

    def __init__(self, limit: int = 50) -> None:
        self._items: deque[FeedItem] = deque(maxlen=limit)
        self._last_id = 0

    def add(self, text: str, link: str | None, when: datetime) -> FeedItem:
        self._last_id += 1
        item = FeedItem(self._last_id, when.isoformat(timespec="seconds"), text, link)
        self._items.append(item)
        return item

    def mark_all_read(self) -> None:
        for item in self._items:
            item.read = True

    @property
    def unread(self) -> int:
        return sum(1 for item in self._items if not item.read)

    @property
    def last_id(self) -> int:
        return self._last_id

    def to_json(self) -> dict[str, Any]:
        return {
            "items": [asdict(item) for item in reversed(self._items)],
            "unread": self.unread,
            "last_id": self._last_id,
        }


class DesktopBot:
    """Stands in for the Telegram bot object: `core.notify()` ends up in `send_message`."""

    def __init__(
        self,
        feed: Feed,
        now: Callable[[], datetime],
        os_enabled: Callable[[], bool],
        window_active: Callable[[], bool],
        show: Callable[[str, str], Any] = show_os_notification,
    ) -> None:
        self.feed, self.now, self.os_enabled, self.window_active, self.show = feed, now, os_enabled, window_active, show

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None, **_: Any) -> None:
        plain, link = to_plain(text)
        self.feed.add(plain, link, self.now())
        # While the window is in front the in-app toast is enough.
        if self.os_enabled() and not self.window_active():
            title, body = split_title(plain)
            await asyncio.to_thread(self.show, title, body)
