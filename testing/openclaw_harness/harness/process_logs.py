"""Tail native uvicorn process log files (replaces docker_logs when no runtime is present)."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Iterable

from . import config


class LogRingbuffer:
    """Fixed-size in-memory log buffer per node; exposes newest-last ordering."""

    def __init__(self, max_lines: int = config.LOG_RINGBUFFER_LINES) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)

    def append(self, line: str) -> None:
        self._lines.append(line.rstrip("\n"))

    def snapshot(self) -> list[str]:
        return list(self._lines)


class NodeLogTailer:
    """Background task that tails a single log file, pushing into a ringbuffer
    and publishing every new line via the event bus."""

    def __init__(self, node_name: str, path: Path) -> None:
        self.node_name = node_name
        self.path = path
        self.buffer = LogRingbuffer()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        from .events import bus

        while not self._stop.is_set():
            if not self.path.exists():
                await asyncio.sleep(0.5)
                continue
            try:
                with self.path.open("r") as fp:
                    fp.seek(0, 2)
                    while not self._stop.is_set():
                        line = fp.readline()
                        if not line:
                            await asyncio.sleep(0.1)
                            continue
                        text = line.rstrip("\n")
                        self.buffer.append(text)
                        await bus.publish(
                            "docker.logline",
                            {"node": self.node_name, "line": text},
                        )
            except FileNotFoundError:
                await asyncio.sleep(0.5)


class FederationLogStreamer:
    """Owns one tailer per federation node."""

    def __init__(self) -> None:
        self.tailers: dict[str, NodeLogTailer] = {
            name: NodeLogTailer(name, path)
            for name, path in config.NODE_LOG_PATHS.items()
        }

    async def start(self) -> None:
        await asyncio.gather(*(t.start() for t in self.tailers.values()))

    async def stop(self) -> None:
        await asyncio.gather(*(t.stop() for t in self.tailers.values()))

    def snapshot(self) -> dict[str, list[str]]:
        return {name: t.buffer.snapshot() for name, t in self.tailers.items()}

    def snapshot_node(self, node_name: str) -> list[str]:
        t = self.tailers.get(node_name)
        return t.buffer.snapshot() if t else []


streamer = FederationLogStreamer()
