"""ConnectionManager bookkeeping.

``ConnectionManager`` is a process-lifetime singleton (``manager`` at module
scope), and ``/system/metrics`` reports its state on every dashboard poll — so
anything it fails to clean up accumulates for as long as the process runs and
shows up in the metrics payload. It had no tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import ConnectionManager


def _socket() -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_broadcast_prunes_a_workflow_whose_last_socket_died():
    """A client that vanishes without a close frame is only discovered on the
    next broadcast. Dropping the socket but keeping its empty set leaves one
    entry per workflow ever streamed to — for the life of the process — and each
    one shows up in the /system/metrics workflows map as a phantom zero."""
    manager = ConnectionManager()
    ws = _socket()
    await manager.connect(ws, "wf-1")
    ws.send_json.side_effect = RuntimeError("socket closed")

    await manager.broadcast_to_workflow("wf-1", {"type": "event"})

    assert manager.total_connections == 0
    assert "wf-1" not in manager.get_status()["workflows"]


@pytest.mark.asyncio
async def test_broadcast_keeps_live_sockets_and_drops_only_the_dead_one():
    manager = ConnectionManager()
    live, dead = _socket(), _socket()
    await manager.connect(live, "wf-1")
    await manager.connect(dead, "wf-1")
    dead.send_json.side_effect = RuntimeError("socket closed")

    await manager.broadcast_to_workflow("wf-1", {"type": "event"})

    assert manager.get_workflow_connections("wf-1") == 1
    assert manager.get_status()["workflows"] == {"wf-1": 1}


@pytest.mark.asyncio
async def test_disconnect_removes_the_workflow_when_its_last_socket_goes():
    manager = ConnectionManager()
    ws = _socket()
    await manager.connect(ws, "wf-1")

    manager.disconnect(ws, "wf-1")

    assert manager.total_connections == 0
    assert manager.get_status()["workflows"] == {}


@pytest.mark.asyncio
async def test_disconnect_of_an_unknown_workflow_creates_no_entry():
    """``_connections`` is a defaultdict, so touching a key is enough to create
    it — a disconnect for a workflow this process never served must not leave
    one behind."""
    manager = ConnectionManager()

    manager.disconnect(_socket(), "never-seen")

    assert manager.get_status()["workflows"] == {}


@pytest.mark.asyncio
async def test_broadcast_to_an_unknown_workflow_creates_no_entry():
    manager = ConnectionManager()

    await manager.broadcast_to_workflow("never-seen", {"type": "event"})

    assert manager.get_status()["workflows"] == {}
