"""📁 Google Drive (optional): a folder per video + daily backup of the bot's data.

Needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN
(get the token once with: python -m creator_bot.tools.google_auth).
Uses the narrow "drive.file" scope: the bot only sees files it created itself.

Listens to: video.created – creates the video's folder with sub-folders.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from datetime import time as clock_time
from typing import Any

import httpx
from telegram import Update
from telegram.ext import JobQueue

from ..config import Settings
from ..core import ui
from ..core.module import Command, Ctx, MenuButton, Module

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"

ROOT_NAME = "🎬 YouTube – Creator Bot"
VIDEO_SUBFOLDERS = ("חומרי גלם", "עריכה", "תמונה ממוזערת")
BACKUP_TIME_HOUR = 3

SCHEMA = [
    """
    CREATE TABLE drive_video_folders (
        video_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        folder_id TEXT NOT NULL,
        url TEXT NOT NULL
    );
    """,
]


class DriveClient:
    """Just the few Drive REST calls the bot needs."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credentials = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        self._transport = transport
        self._token: str | None = None
        self._token_expires = 0.0

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=120, transport=self._transport) as client:
            if self._token is None or time.monotonic() > self._token_expires - 60:
                response = await client.post(TOKEN_URL, data=self._credentials)
                response.raise_for_status()
                body = response.json()
                self._token = body["access_token"]
                self._token_expires = time.monotonic() + body.get("expires_in", 3600)
            headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
            response = await client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    async def create_folder(self, name: str, parent: str | None = None) -> dict[str, str]:
        metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            metadata["parents"] = [parent]
        response = await self._request("POST", FILES_URL, params={"fields": "id,webViewLink"}, json=metadata)
        return response.json()

    async def upload(self, name: str, content: bytes, parent: str, file_id: str | None = None) -> str:
        """Create the file (or overwrite `file_id`) and return its id."""
        if file_id is None:
            response = await self._request(
                "POST", FILES_URL, params={"fields": "id"}, json={"name": name, "parents": [parent]}
            )
            file_id = response.json()["id"]
        await self._request(
            "PATCH",
            f"{UPLOAD_URL}/{file_id}",
            params={"uploadType": "media"},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        return file_id


def _is_not_found(error: Exception) -> bool:
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404


class DriveModule(Module):
    name = "drive"
    client_transport: httpx.AsyncBaseTransport | None = None  # tests swap the network

    @classmethod
    def is_enabled(cls, settings: Settings) -> bool:
        return bool(settings.google_client_id and settings.google_client_secret and settings.google_refresh_token)

    def setup(self) -> None:
        self.core.db.migrate(self.name, SCHEMA)
        s = self.core.settings
        self.client = DriveClient(
            s.google_client_id, s.google_client_secret, s.google_refresh_token, self.client_transport
        )
        self.core.events.on("video.created", self._on_video_created)

    def menu_buttons(self) -> list[MenuButton]:
        return [MenuButton("📁 דרייב", self.show_folders, order=60)]

    def commands(self) -> list[Command]:
        return [
            Command("drive", "התיקיות בגוגל דרייב", self.show_folders),
            Command("backup", "גיבוי הנתונים לדרייב עכשיו", self.cmd_backup),
        ]

    def schedule(self, job_queue: JobQueue) -> None:
        job_queue.run_daily(self._backup_job, clock_time(BACKUP_TIME_HOUR, 0), name="drive_backup")

    # -- folders ----------------------------------------------------------------
    async def _folder(self, key: str, name: str, parent: str | None) -> dict[str, str]:
        """A folder the bot created once and remembers (id + link) in the kv store."""
        saved = self.core.db.get(f"drive.{key}")
        if saved:
            return saved
        folder = await self.client.create_folder(name, parent)
        self.core.db.set(f"drive.{key}", folder)
        return folder

    async def folders(self) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        root = await self._folder("root", ROOT_NAME, None)
        videos = await self._folder("videos", "סרטונים", root["id"])
        backups = await self._folder("backups", "גיבויים", root["id"])
        return root, videos, backups

    def _forget_folders(self) -> None:
        """The user deleted our folders in Drive – recreate them next time."""
        for key in ("root", "videos", "backups"):
            self.core.db.set(f"drive.{key}", None)
        for weekday in range(7):
            self.core.db.set(f"drive.backup_file.{weekday}", None)

    async def create_video_folder(self, video: Any) -> dict[str, str]:
        for attempt in range(2):
            try:
                _, videos, _ = await self.folders()
                folder = await self.client.create_folder(
                    f"{video.publish_date.isoformat()} · {video.title}", videos["id"]
                )
                break
            except httpx.HTTPStatusError as error:
                if attempt == 0 and _is_not_found(error):
                    self._forget_folders()
                    continue
                raise
        for sub in VIDEO_SUBFOLDERS:
            await self.client.create_folder(sub, folder["id"])
        self.core.db.execute(
            "INSERT OR REPLACE INTO drive_video_folders (video_id, title, folder_id, url) VALUES (?, ?, ?, ?)",
            (video.id, video.title, folder["id"], folder["webViewLink"]),
        )
        return folder

    async def _on_video_created(self, video: Any, **_: Any) -> None:
        try:
            folder = await self.create_video_folder(video)
        except httpx.HTTPError:
            log.exception("Could not create a Drive folder for video %s", video.id)
            await self.core.notify("😕 לא הצלחתי לפתוח תיקייה בדרייב לסרטון. אפשר לבדוק את הגדרות גוגל ב-.env.")
            return
        await self.core.notify(
            f"📁 פתחתי תיקייה בדרייב ל<b>{ui.esc(video.title)}</b>:\n"
            f'<a href="{folder["webViewLink"]}">פתיחת התיקייה</a> ({" · ".join(VIDEO_SUBFOLDERS)})'
        )

    async def show_folders(self, update: Update, context: Ctx) -> None:
        try:
            root, _, _ = await self.folders()
        except httpx.HTTPError:
            log.exception("Could not reach Google Drive")
            await ui.reply(update, "😕 לא הצלחתי להתחבר לגוגל דרייב. כדאי לבדוק את הגדרות GOOGLE_ ב-.env.")
            return
        lines = ["📁 <b>גוגל דרייב</b>", f'<a href="{root["webViewLink"]}">התיקייה הראשית של הבוט</a>']
        rows = self.core.db.query("SELECT title, url FROM drive_video_folders ORDER BY video_id DESC LIMIT 8")
        if rows:
            lines += ["", "<b>סרטונים אחרונים:</b>"]
            lines += [f'• <a href="{row["url"]}">{ui.esc(row["title"])}</a>' for row in rows]
        await ui.reply(update, "\n".join(lines))

    # -- backup -----------------------------------------------------------------
    async def backup(self, day: date) -> dict[str, str]:
        """Upload a copy of the database. One file per weekday = 7 rotating backups."""
        weekday = day.weekday()
        key = f"drive.backup_file.{weekday}"
        content = self.core.db.snapshot()
        for attempt in range(3):
            try:
                _, _, backups = await self.folders()
                file_id = await self.client.upload(
                    f"creator-bot-backup-{weekday + 1}.db", content, backups["id"], self.core.db.get(key)
                )
                break
            except httpx.HTTPStatusError as error:
                if attempt == 2 or not _is_not_found(error):
                    raise
                if self.core.db.get(key):
                    self.core.db.set(key, None)  # the backup file was deleted – start a new one
                else:
                    self._forget_folders()  # the folders were deleted
        self.core.db.set(key, file_id)
        return backups

    async def _backup_job(self, context: Ctx) -> None:
        try:
            await self.backup(self.core.today())
        except httpx.HTTPError:
            log.exception("Daily Drive backup failed")

    async def cmd_backup(self, update: Update, context: Ctx) -> None:
        try:
            backups = await self.backup(self.core.today())
        except httpx.HTTPError:
            log.exception("Drive backup failed")
            await ui.reply(update, "😕 הגיבוי נכשל. כדאי לבדוק את הגדרות GOOGLE_ ב-.env.")
            return
        await ui.reply(update, f'💾 גובה בהצלחה! <a href="{backups["webViewLink"]}">לתיקיית הגיבויים</a>')
