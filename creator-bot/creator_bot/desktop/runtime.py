"""Runs the app logic on its own asyncio thread; other threads submit requests to it.

The HTTP server (many threads) and the window (main thread) never touch the
database directly – they call `Runtime.request()`, which hands the work to the
one thread that owns the SQLite connection and the reminder scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from .app import DesktopApp

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, factory: Callable[[], DesktopApp], tick_seconds: float = 30) -> None:
        self._factory = factory
        self._tick_seconds = tick_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, name="creator-desk-logic", daemon=True)
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.app: DesktopApp
        self.quit_event = threading.Event()

    def start(self) -> None:
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self.app = self._factory()  # created here: SQLite connections belong to their thread
            self.app.quit_callback = self.quit_event.set
            scheduler = loop.create_task(self._scheduler())
        except BaseException as error:  # reported to start()
            self._error = error
            return
        finally:
            self._ready.set()
        loop.run_forever()
        scheduler.cancel()
        loop.run_until_complete(asyncio.gather(scheduler, return_exceptions=True))
        loop.run_until_complete(self.app.idle())
        self.app.db.close()
        loop.close()

    async def _scheduler(self) -> None:
        while True:
            try:
                await self.app.tick()
            except Exception:
                log.exception("Reminder check failed")
            await asyncio.sleep(self._tick_seconds)

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        body: dict[str, Any],
        timeout: float = 130,  # the assistant may think for a while
    ) -> tuple[int, Any]:
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self.app.handle(method, path, query, body), self._loop)
        return future.result(timeout)

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)
