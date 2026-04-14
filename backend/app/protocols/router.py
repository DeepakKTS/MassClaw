"""Protocol router — dispatches messages to the correct adapter."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.agent import Agent
from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType
from app.protocols.http_adapter import HTTPAdapter
from app.protocols.websocket_adapter import WebSocketAdapter

logger = get_logger(__name__)


class ProtocolRouter:
    """Routes outgoing messages to the appropriate protocol adapter.

    Resolves the target agent's ``protocol_type`` and delegates to the
    matching adapter (HTTP or WebSocket).  Falls back to HTTP for unknown
    or unset protocol types.
    """

    def __init__(self) -> None:
        self._http = HTTPAdapter()
        self._ws = WebSocketAdapter()

        self._adapters: dict[ProtocolType, ProtocolAdapter] = {
            ProtocolType.HTTP: self._http,
            ProtocolType.WEBSOCKET: self._ws,
        }

    # ------------------------------------------------------------------
    # Adapter lookup
    # ------------------------------------------------------------------

    def get_adapter(self, protocol_type: ProtocolType) -> ProtocolAdapter:
        """Return the adapter for *protocol_type*.

        Falls back to the HTTP adapter for unknown types.
        """
        adapter = self._adapters.get(protocol_type)
        if adapter is None:
            logger.warning(
                "protocol_router_unknown_type",
                protocol_type=str(protocol_type),
                fallback="http",
            )
            return self._http
        return adapter

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _resolve_protocol(self, agent: Agent) -> ProtocolType:
        """Determine the protocol type for *agent*.

        Reads ``agent.protocol_type`` when present; defaults to HTTP.
        """
        raw = getattr(agent, "protocol_type", None)
        if raw is None:
            return ProtocolType.HTTP
        if isinstance(raw, ProtocolType):
            return raw
        try:
            return ProtocolType(raw)
        except ValueError:
            return ProtocolType.HTTP

    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        """Route *message* to *agent* via the agent's protocol adapter."""
        protocol = self._resolve_protocol(agent)
        adapter = self.get_adapter(protocol)
        logger.debug(
            "protocol_router_send",
            agent_id=str(agent.agent_id),
            protocol=protocol.value,
        )
        return await adapter.send_message(agent, message)

    async def health_check(self, agent: Agent) -> bool:
        """Check whether *agent* is reachable via its protocol adapter."""
        protocol = self._resolve_protocol(agent)
        adapter = self.get_adapter(protocol)
        return await adapter.health_check(agent)
