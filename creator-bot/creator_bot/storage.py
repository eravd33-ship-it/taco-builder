"""SQLite storage shared by all modules.

Each module owns its tables and declares them through `migrate()`, so adding a
feature never requires touching another module's schema.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    # Key/value store for small settings (goals, owner id, Drive folder ids...).
    def get(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        if value is None:
            self.execute("DELETE FROM kv WHERE key = ?", (key,))
            return
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def migrate(self, namespace: str, steps: Sequence[str]) -> None:
        """Apply the schema scripts of `namespace` that were not applied yet.

        Never edit a step that already shipped – append a new one instead.
        """
        version = self.get(f"schema.{namespace}", 0)
        for number, script in enumerate(steps[version:], start=version + 1):
            self.conn.executescript(script)
            self.set(f"schema.{namespace}", number)

    def snapshot(self) -> bytes:
        """A consistent copy of the whole database, e.g. for backups."""
        with tempfile.TemporaryDirectory() as tmp:
            target_path = Path(tmp) / "backup.db"
            target = sqlite3.connect(target_path)
            try:
                self.conn.backup(target)
            finally:
                target.close()
            return target_path.read_bytes()
