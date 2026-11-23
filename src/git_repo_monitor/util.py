"""Small shared helpers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Hashable
from datetime import UTC, datetime

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Return a lowercase identifier usable in MQTT topics and entity ids."""
    slug = _SLUG_RE.sub("_", value.lower()).strip("_")
    return slug or "unnamed"


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Debouncer:
    """Coalesce bursts of triggers for the same key into a single callback."""

    def __init__(self, delay: float, callback: Callable[[Hashable], Awaitable[None]]) -> None:
        self._delay = delay
        self._callback = callback
        self._pending: dict[Hashable, asyncio.Task[None]] = {}

    def trigger(self, key: Hashable) -> None:
        if key in self._pending:
            return
        self._pending[key] = asyncio.create_task(self._run(key))

    async def _run(self, key: Hashable) -> None:
        try:
            await asyncio.sleep(self._delay)
        finally:
            self._pending.pop(key, None)
        await self._callback(key)

    @property
    def pending(self) -> set[Hashable]:
        return set(self._pending)

    def cancel(self) -> None:
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()
