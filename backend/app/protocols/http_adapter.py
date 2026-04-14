"""HTTP protocol adapter for agent communication."""

from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.models.agent import Agent
from app.protocols.base import AgentMessage, ProtocolAdapter, ProtocolType

logger = get_logger(__name__)


class HTTPAdapter(ProtocolAdapter):
    """Sends agent messages over HTTP and performs HTTP health checks."""

    protocol_type = ProtocolType.HTTP

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def send_message(self, agent: Agent, message: AgentMessage) -> AgentMessage | None:
        """POST the message JSON to the agent's endpoint and parse the response.

        Returns an AgentMessage built from the response body, or None if the
        agent returns no body or an error response.
        """
        if not agent.endpoint:
            return None

        import dataclasses

        payload = dataclasses.asdict(message)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(agent.endpoint, json=payload)
                response.raise_for_status()

            body = response.json()
            if body:
                return AgentMessage(**body)
            return None

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "http_adapter_send_error",
                agent_id=str(agent.agent_id),
                status_code=exc.response.status_code,
                error=str(exc),
            )
            return None
        except Exception as exc:
            logger.warning(
                "http_adapter_send_exception",
                agent_id=str(agent.agent_id),
                error=str(exc),
            )
            return None

    async def health_check(self, agent: Agent) -> bool:
        """GET the agent's health_check_url and return True for 2xx responses."""
        if not agent.health_check_url:
            logger.debug("http_health_check_no_url", agent_id=str(agent.agent_id))
            return False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(agent.health_check_url)
            healthy = response.is_success
            logger.debug(
                "http_health_check",
                agent_id=str(agent.agent_id),
                status_code=response.status_code,
                healthy=healthy,
            )
            return healthy
        except Exception as exc:
            logger.warning(
                "http_health_check_exception",
                agent_id=str(agent.agent_id),
                error=str(exc),
            )
            return False
