"""A tiny async event bus, so modules can react to each other without imports.

Example: the videos module emits "video.published"; the motivation module
listens and celebrates, and the Drive module could archive files. Handlers
receive keyword arguments and should accept **_ for forward compatibility.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

EventHandler = Callable[..., Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event: str, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        if handler in self._handlers.get(event, []):
            self._handlers[event].remove(handler)

    async def emit(self, event: str, **payload: Any) -> None:
        for handler in self._handlers.get(event, []):
            try:
                await handler(**payload)
            except Exception:
                # One failing listener must not break the action that emitted the event.
                log.exception("Handler %r failed for event %s", handler, event)
