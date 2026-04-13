from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.events import EventBus
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections with Redis pub/sub bridge.

    Features:
    - Tracks connections per workflow for targeted broadcasting
    - Bridges Redis pub/sub events to WebSocket clients
    - Heartbeat every 30s to detect stale connections
    - Bidirectional: clients can send commands (pause, cancel)
    - Clean disconnect handling with automatic unsubscribe
    """

    HEARTBEAT_INTERVAL = 30  # seconds

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def total_connections(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    def get_workflow_connections(self, workflow_id: str) -> int:
        return len(self._connections.get(workflow_id, set()))

    async def connect(self, websocket: WebSocket, workflow_id: str) -> None:
        """Accept a WebSocket connection and register it for a workflow."""
        await websocket.accept()
        self._connections[workflow_id].add(websocket)

        logger.info(
            "ws_connected",
            workflow_id=workflow_id,
            total=self.total_connections,
        )

        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "workflow_id": workflow_id,
            "message": "Connected to MassClaw workflow stream",
        })

    def disconnect(self, websocket: WebSocket, workflow_id: str) -> None:
        """Remove a WebSocket connection."""
        self._connections[workflow_id].discard(websocket)
        if not self._connections[workflow_id]:
            del self._connections[workflow_id]

        logger.info(
            "ws_disconnected",
            workflow_id=workflow_id,
            total=self.total_connections,
        )

    async def broadcast_to_workflow(self, workflow_id: str, data: dict) -> None:
        """Send a message to all WebSocket connections for a workflow."""
        dead: list[WebSocket] = []
        for ws in self._connections.get(workflow_id, set()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections[workflow_id].discard(ws)

    async def broadcast_all(self, data: dict) -> None:
        """Send a message to ALL connected WebSocket clients."""
        for workflow_id in list(self._connections.keys()):
            await self.broadcast_to_workflow(workflow_id, data)

    def get_status(self) -> dict:
        return {
            "total_connections": self.total_connections,
            "workflows": {
                wf_id: len(conns)
                for wf_id, conns in self._connections.items()
            },
        }


# Module-level singleton
manager = ConnectionManager()


def get_ws_manager() -> ConnectionManager:
    return manager


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/workflows/{workflow_id}")
async def workflow_websocket(websocket: WebSocket, workflow_id: str) -> None:
    """WebSocket endpoint for real-time workflow progress.

    Bridges Redis pub/sub events to the client. Supports bidirectional
    communication — clients can send commands like pause/cancel.

    Protocol:
    - Server -> Client: JSON events from Redis pub/sub
    - Client -> Server: JSON commands {"action": "pause|cancel|ping"}
    - Server sends heartbeat pings every 30s
    """
    await manager.connect(websocket, workflow_id)

    # Start background tasks for this connection
    pubsub_task = asyncio.create_task(
        _bridge_pubsub_to_ws(websocket, workflow_id)
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat(websocket)
    )

    try:
        # Listen for client messages (commands)
        while True:
            try:
                raw = await websocket.receive_text()
                await _handle_client_message(websocket, workflow_id, raw)
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.warning("ws_error", workflow_id=workflow_id, error=str(e))
    finally:
        pubsub_task.cancel()
        heartbeat_task.cancel()
        manager.disconnect(websocket, workflow_id)

        # Wait for tasks to clean up
        for task in (pubsub_task, heartbeat_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def _bridge_pubsub_to_ws(websocket: WebSocket, workflow_id: str) -> None:
    """Subscribe to Redis pub/sub for this workflow and forward events to WebSocket."""
    try:
        async for event in EventBus.subscribe("workflow", workflow_id, "*"):
            try:
                await websocket.send_json({
                    "type": "event",
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "data": event.data,
                })
            except Exception:
                break  # WebSocket closed
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug("pubsub_bridge_ended", workflow_id=workflow_id, reason=str(e))


async def _heartbeat(websocket: WebSocket) -> None:
    """Send periodic heartbeat pings to keep the connection alive."""
    try:
        while True:
            await asyncio.sleep(ConnectionManager.HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json({"type": "heartbeat", "ts": str(uuid4())[:8]})
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _handle_client_message(
    websocket: WebSocket, workflow_id: str, raw: str
) -> None:
    """Handle incoming messages from the WebSocket client."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
        return

    action = msg.get("action", "")

    if action == "ping":
        await websocket.send_json({"type": "pong"})

    elif action == "pause":
        # Publish pause command to the workflow's event channel
        await EventBus.publish_dict(
            ["workflow", workflow_id, "command"],
            "workflow.command.pause",
            {"workflow_id": workflow_id, "command": "pause"},
        )
        await websocket.send_json({"type": "ack", "action": "pause"})

    elif action == "cancel":
        await EventBus.publish_dict(
            ["workflow", workflow_id, "command"],
            "workflow.command.cancel",
            {"workflow_id": workflow_id, "command": "cancel"},
        )
        await websocket.send_json({"type": "ack", "action": "cancel"})

    elif action == "status":
        await websocket.send_json({
            "type": "status",
            "connections": manager.get_status(),
        })

    else:
        await websocket.send_json({
            "type": "error",
            "detail": f"Unknown action: {action}",
            "supported": ["ping", "pause", "cancel", "status"],
        })


# ---------------------------------------------------------------------------
# Dashboard-level WebSocket (all events)
# ---------------------------------------------------------------------------


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket) -> None:
    """WebSocket for the global dashboard — receives ALL system events.

    Subscribes to massclaw:* pattern to capture workflow progress,
    agent health, trust updates, wallet transactions, etc.
    """
    await websocket.accept()

    logger.info("dashboard_ws_connected")

    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        async for channel, event in EventBus.subscribe_multiple("*"):
            try:
                await websocket.send_json({
                    "type": "event",
                    "channel": channel,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "data": event.data,
                })
            except Exception:
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug("dashboard_ws_ended", reason=str(e))
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("dashboard_ws_disconnected")
