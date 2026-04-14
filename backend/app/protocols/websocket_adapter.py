"""WebSocket protocol adapter for agent communication."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from app.core.logging import get_logger
from app.models.agent import Agent
from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType

logger = get_logger(__name__)


class WebSocketAdapter(ProtocolAdapter):
    """Maintains persistent WebSocket connections to agents.

    Connections are keyed by ``str(agent.agent_id)`` and reused across calls.
    """

    protocol_type = ProtocolType.WEBSOCKET

    def __init__(self) -> None:
        self._connections: dict[str, Any] = {}  # agent_id -> websockets connection

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        """Connect if needed, send message JSON, wait for response.

        Returns an AgentMessage parsed from the server's reply, or None on error.
        """
        agent_key = str(agent.agent_id)
        ws = await self._get_or_connect(agent)
        if ws is None:
            return None

        try:
            payload = json.dumps(dataclasses.asdict(message))
            await ws.send(payload)
            raw = await ws.recv()
            data = json.loads(raw)
            return AgentMessage(**data)
        except Exception as exc:
            logger.warning(
                "ws_send_error",
                agent_id=agent_key,
                error=str(exc),
            )
            # Drop the broken connection so next call reconnects
            self._connections.pop(agent_key, None)
            return None

    async def health_check(self, agent: Agent) -> bool:
        """Ping the WebSocket connection to verify liveness.

        Returns True if the agent is reachable, False otherwise.
        """
        agent_key = str(agent.agent_id)
        ws = self._connections.get(agent_key)

        if ws is None:
            # Attempt a fresh connection as the health signal
            ws = await self._connect(agent)
            return ws is not None

        try:
            pong = await ws.ping()
            await pong
            return True
        except Exception as exc:
            logger.warning(
                "ws_ping_failed",
                agent_id=agent_key,
                error=str(exc),
            )
            self._connections.pop(agent_key, None)
            return False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def _connect(self, agent: Agent) -> Any | None:
        """Establish a new WebSocket connection to *agent.endpoint*.

        Returns the websockets connection object, or None on failure.
        """
        import websockets

        agent_key = str(agent.agent_id)
        try:
            ws = await websockets.connect(agent.endpoint)
            self._connections[agent_key] = ws
            logger.debug("ws_connected", agent_id=agent_key, endpoint=agent.endpoint)
            return ws
        except Exception as exc:
            logger.warning(
                "ws_connect_failed",
                agent_id=agent_key,
                endpoint=agent.endpoint,
                error=str(exc),
            )
            return None

    async def _get_or_connect(self, agent: Agent) -> Any | None:
        """Return an existing connection or create a new one."""
        agent_key = str(agent.agent_id)
        if agent_key not in self._connections:
            return await self._connect(agent)
        return self._connections[agent_key]

    async def disconnect(self, agent_id: str) -> None:
        """Close and remove the connection for *agent_id*."""
        ws = self._connections.pop(agent_id, None)
        if ws is not None:
            try:
                await ws.close()
                logger.debug("ws_disconnected", agent_id=agent_id)
            except Exception as exc:
                logger.warning("ws_close_error", agent_id=agent_id, error=str(exc))
