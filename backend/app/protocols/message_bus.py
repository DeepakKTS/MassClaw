"""Agent Message Bus — Redis pub/sub agent-to-agent messaging."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis

from app.core.logging import get_logger
from app.protocols.base import AgentMessage

logger = get_logger(__name__)

_DIRECT_CHANNEL_PREFIX = "massclaw:agent:direct:"
_CAPABILITY_CHANNEL_PREFIX = "massclaw:agent:capability:"


def _message_to_dict(msg: AgentMessage) -> dict[str, Any]:
    return dataclasses.asdict(msg)


def _message_from_dict(data: dict[str, Any]) -> AgentMessage:
    return AgentMessage(**data)


class AgentMessageBus:
    """Redis-backed pub/sub message bus for agent-to-agent communication."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def send_direct(
        self,
        sender_id: str,
        recipient_id: str,
        content: str,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Publish a direct message to a specific agent.

        Returns the AgentMessage that was published.
        """
        msg = AgentMessage(
            sender_id=sender_id,
            recipient_id=recipient_id,
            channel=f"{_DIRECT_CHANNEL_PREFIX}{recipient_id}",
            content=content,
            message_type="request",
            correlation_id=correlation_id,
        )
        payload = json.dumps(_message_to_dict(msg))
        await self._redis.publish(msg.channel, payload)
        logger.debug(
            "direct_message_sent",
            sender=sender_id,
            recipient=recipient_id,
            message_id=msg.message_id,
        )
        return msg

    async def broadcast_capability(
        self,
        sender_id: str,
        capability: str,
        content: str,
    ) -> AgentMessage:
        """Broadcast a message to all agents subscribed to a capability channel.

        Returns the AgentMessage that was published.
        """
        channel = f"{_CAPABILITY_CHANNEL_PREFIX}{capability}"
        msg = AgentMessage(
            sender_id=sender_id,
            channel=channel,
            content=content,
            message_type="notification",
        )
        payload = json.dumps(_message_to_dict(msg))
        await self._redis.publish(channel, payload)
        logger.debug(
            "capability_broadcast_sent",
            sender=sender_id,
            capability=capability,
            message_id=msg.message_id,
        )
        return msg

    # ------------------------------------------------------------------
    # Subscribing
    # ------------------------------------------------------------------

    async def subscribe_direct(self, agent_id: str) -> AsyncIterator[AgentMessage]:
        """Async iterator that yields AgentMessages sent directly to *agent_id*."""
        channel = f"{_DIRECT_CHANNEL_PREFIX}{agent_id}"
        async with self._redis.pubsub() as ps:
            await ps.subscribe(channel)
            async for raw in ps.listen():
                if raw["type"] == "message":
                    try:
                        data = json.loads(raw["data"])
                        yield _message_from_dict(data)
                    except Exception as exc:
                        logger.warning("direct_message_parse_error", error=str(exc))

    async def subscribe_capability(self, capability: str) -> AsyncIterator[AgentMessage]:
        """Async iterator that yields AgentMessages broadcast to *capability*."""
        channel = f"{_CAPABILITY_CHANNEL_PREFIX}{capability}"
        async with self._redis.pubsub() as ps:
            await ps.subscribe(channel)
            async for raw in ps.listen():
                if raw["type"] == "message":
                    try:
                        data = json.loads(raw["data"])
                        yield _message_from_dict(data)
                    except Exception as exc:
                        logger.warning("capability_message_parse_error", error=str(exc))

    # ------------------------------------------------------------------
    # Request / Response
    # ------------------------------------------------------------------

    async def request_response(
        self,
        sender_id: str,
        recipient_id: str,
        content: str,
        timeout: float = 30.0,
    ) -> AgentMessage:
        """Send a request and wait for a correlated response.

        Opens a temporary subscription on the *sender's* direct channel,
        sends the request with a unique correlation_id, then waits up to
        *timeout* seconds for a response carrying the same correlation_id.

        Raises asyncio.TimeoutError if no correlated response arrives in time.
        """
        correlation_id = str(uuid4())
        reply_channel = f"{_DIRECT_CHANNEL_PREFIX}{sender_id}"

        async with self._redis.pubsub() as ps:
            await ps.subscribe(reply_channel)

            # Send the request
            msg = AgentMessage(
                sender_id=sender_id,
                recipient_id=recipient_id,
                channel=f"{_DIRECT_CHANNEL_PREFIX}{recipient_id}",
                content=content,
                message_type="request",
                correlation_id=correlation_id,
            )
            payload = json.dumps(_message_to_dict(msg))
            await self._redis.publish(msg.channel, payload)

            async def _wait_for_response() -> AgentMessage:
                async for raw in ps.listen():
                    if raw["type"] != "message":
                        continue
                    try:
                        data = json.loads(raw["data"])
                        candidate = _message_from_dict(data)
                        if candidate.correlation_id == correlation_id:
                            return candidate
                    except Exception as exc:
                        logger.warning("request_response_parse_error", error=str(exc))

                # Should never reach here normally
                raise RuntimeError("Pub/sub stream closed unexpectedly")

            return await asyncio.wait_for(_wait_for_response(), timeout=timeout)
