from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.redis import get_redis_manager


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------


class BaseEvent(BaseModel):
    """Base class for all MassClaw events."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed event models
# ---------------------------------------------------------------------------


class WorkflowProgressEvent(BaseEvent):
    """Fired during workflow execution for real-time DAG updates."""

    event_type: str = "workflow.progress"

    @staticmethod
    def create(
        workflow_id: str,
        event: str,
        progress_percent: float,
        completed: int,
        failed: int,
        total: int,
        node_id: str | None = None,
        capability: str | None = None,
        agent_name: str | None = None,
        status: str | None = None,
        **extra: Any,
    ) -> WorkflowProgressEvent:
        return WorkflowProgressEvent(
            event_type=f"workflow.{event}",
            data={
                "workflow_id": workflow_id,
                "event": event,
                "progress_percent": progress_percent,
                "completed": completed,
                "failed": failed,
                "total": total,
                "node_id": node_id,
                "capability": capability,
                "agent_name": agent_name,
                "status": status,
                **extra,
            },
        )


class AgentHealthEvent(BaseEvent):
    """Fired when agent health status changes."""

    event_type: str = "agent.health_changed"

    @staticmethod
    def create(
        agent_id: str,
        agent_name: str,
        old_status: str,
        new_status: str,
        healthy: bool,
        latency_ms: float | None = None,
    ) -> AgentHealthEvent:
        return AgentHealthEvent(
            data={
                "agent_id": agent_id,
                "agent_name": agent_name,
                "old_status": old_status,
                "new_status": new_status,
                "healthy": healthy,
                "latency_ms": latency_ms,
            },
        )


class TrustUpdateEvent(BaseEvent):
    """Fired when an agent's trust score is updated."""

    event_type: str = "trust.updated"

    @staticmethod
    def create(
        agent_id: str,
        old_trust: float,
        new_trust: float,
        composite: float,
    ) -> TrustUpdateEvent:
        return TrustUpdateEvent(
            data={
                "agent_id": agent_id,
                "old_trust": old_trust,
                "new_trust": new_trust,
                "composite": composite,
            },
        )


class WalletTransactionEvent(BaseEvent):
    """Fired on any wallet mutation (reserve, charge, release, credit)."""

    event_type: str = "wallet.transaction"

    @staticmethod
    def create(
        workflow_id: str,
        agent_id: str | None,
        action_type: str,
        amount: float,
        balance_after: float,
    ) -> WalletTransactionEvent:
        return WalletTransactionEvent(
            data={
                "workflow_id": workflow_id,
                "agent_id": agent_id,
                "action_type": action_type,
                "amount": amount,
                "balance_after": balance_after,
            },
        )


class PolicyDecisionEvent(BaseEvent):
    """Fired when a policy rule is evaluated."""

    event_type: str = "policy.evaluated"

    @staticmethod
    def create(
        action: str,
        approved: bool,
        matched_rule: str | None,
        workflow_id: str | None = None,
    ) -> PolicyDecisionEvent:
        return PolicyDecisionEvent(
            data={
                "action": action,
                "approved": approved,
                "matched_rule": matched_rule,
                "workflow_id": workflow_id,
            },
        )


class MemoryWriteEvent(BaseEvent):
    """Fired when a memory record is created or updated."""

    event_type: str = "memory.written"

    @staticmethod
    def create(
        memory_id: str,
        workflow_id: str,
        source_agent_id: str | None,
        memory_type: str,
        version: int,
        confidence: float,
    ) -> MemoryWriteEvent:
        return MemoryWriteEvent(
            data={
                "memory_id": memory_id,
                "workflow_id": workflow_id,
                "source_agent_id": source_agent_id,
                "memory_type": memory_type,
                "version": version,
                "confidence": confidence,
            },
        )


class ScoreRecordedEvent(BaseEvent):
    """Fired when an agent evolution score is recorded."""

    event_type: str = "score.recorded"

    @staticmethod
    def create(agent_id: str, composite: float) -> ScoreRecordedEvent:
        return ScoreRecordedEvent(
            data={"agent_id": agent_id, "composite": composite},
        )


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------


class EventBus:
    """Redis pub/sub event bus for real-time inter-component communication.

    Channel naming: massclaw:{entity_type}:{entity_id}:{event_type}
    Supports wildcard subscriptions via '*' for dashboard monitoring.
    """

    CHANNEL_PREFIX = "massclaw"

    @staticmethod
    def _channel_name(*parts: str) -> str:
        return ":".join([EventBus.CHANNEL_PREFIX, *parts])

    @staticmethod
    async def publish(channel_parts: list[str], event: BaseEvent) -> int:
        """Publish an event to a Redis channel."""
        try:
            redis = get_redis_manager().get_pubsub_client()
            channel = EventBus._channel_name(*channel_parts)
            payload = event.model_dump_json()
            return await redis.publish(channel, payload)
        except Exception:
            return 0  # Silently fail — events are best-effort

    @staticmethod
    async def subscribe(*channel_parts: str) -> AsyncIterator[BaseEvent]:
        """Subscribe to a Redis channel and yield events.

        Supports '*' wildcards for pattern matching.
        """
        redis = get_redis_manager().get_pubsub_client()
        channel = EventBus._channel_name(*channel_parts)
        pubsub = redis.pubsub()

        if "*" in channel:
            await pubsub.psubscribe(channel)
        else:
            await pubsub.subscribe(channel)

        try:
            async for message in pubsub.listen():
                if message["type"] in ("message", "pmessage"):
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        data = json.loads(raw)
                        yield BaseEvent(**data)
                    except (json.JSONDecodeError, Exception):
                        continue  # Skip malformed messages
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    @staticmethod
    async def publish_dict(channel_parts: list[str], event_type: str, data: dict[str, Any]) -> int:
        """Convenience method to publish a dict as an event."""
        event = BaseEvent(event_type=event_type, data=data)
        return await EventBus.publish(channel_parts, event)

    @staticmethod
    async def subscribe_multiple(*patterns: str) -> AsyncIterator[tuple[str, BaseEvent]]:
        """Subscribe to multiple channel patterns, yielding (channel, event) tuples."""
        redis = get_redis_manager().get_pubsub_client()
        pubsub = redis.pubsub()

        channels = [EventBus._channel_name(*p.split(":")) if ":" in p else EventBus._channel_name(p) for p in patterns]

        for ch in channels:
            if "*" in ch:
                await pubsub.psubscribe(ch)
            else:
                await pubsub.subscribe(ch)

        try:
            async for message in pubsub.listen():
                if message["type"] in ("message", "pmessage"):
                    raw = message["data"]
                    channel = message.get("channel", b"").decode("utf-8") if isinstance(message.get("channel"), bytes) else message.get("channel", "")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        data = json.loads(raw)
                        yield channel, BaseEvent(**data)
                    except (json.JSONDecodeError, Exception):
                        continue
        finally:
            await pubsub.unsubscribe()
            await pubsub.punsubscribe()
            await pubsub.aclose()
