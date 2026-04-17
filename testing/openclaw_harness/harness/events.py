"""In-process async pub/sub — no Redis needed (harness is single-process)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class Event:
    topic: str
    payload: dict[str, Any]


class EventBus:
    """Simple fan-out bus. Every subscriber gets its own queue."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers.append(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        async with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        event = Event(topic=topic, payload=payload)
        async with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


bus = EventBus()
