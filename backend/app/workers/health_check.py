"""Periodic agent health monitoring with automatic status transitions.

Health check logic:
- For each active/degraded agent with a health_check_url:
  - HTTP GET with 5s timeout via httpx
  - Track consecutive failures in Redis (agent_health_failures:{agent_id})
  - 3 consecutive failures -> status = degraded, publish AgentHealthEvent
  - 5 consecutive failures -> status = suspended, publish AgentHealthEvent
  - Success after degraded -> status = active (recovery), publish event
  - Update agent.last_health_check timestamp
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update

from app.config import get_settings
from app.core.database import db_session_context, init_db
from app.core.events import AgentHealthEvent, EventBus
from app.core.logging import get_logger
from app.core.redis import get_redis_manager, init_redis
from app.models.agent import Agent
from app.models.base import AgentStatus

logger = get_logger(__name__)

REDIS_FAILURE_KEY_PREFIX = "agent_health_failures:"


async def run_health_checks() -> dict:
    """Main function called by the Celery task.

    Iterates over all active/degraded agents with a health_check_url,
    performs an HTTP health check, and transitions status based on
    consecutive failure counts tracked in Redis.

    Returns:
        Summary dict: {"checked": N, "healthy": N, "degraded": N, "suspended": N}
    """
    settings = get_settings()
    init_db()
    await init_redis()

    summary = {"checked": 0, "healthy": 0, "degraded": 0, "suspended": 0}

    async with db_session_context() as session:
        redis = get_redis_manager().get_cache_client()

        # Fetch all agents that are active or degraded and have a health check URL
        result = await session.execute(
            select(Agent).where(
                Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.DEGRADED]),
                Agent.health_check_url.isnot(None),
            )
        )
        agents = list(result.scalars().all())

        timeout = settings.health_check_timeout_seconds
        degraded_threshold = settings.health_check_degraded_threshold
        suspended_threshold = settings.health_check_suspended_threshold

        async with httpx.AsyncClient(timeout=timeout) as client:
            for agent in agents:
                summary["checked"] += 1
                failure_key = f"{REDIS_FAILURE_KEY_PREFIX}{agent.agent_id}"
                old_status = agent.status

                healthy = await _check_agent_health(client, agent.health_check_url)

                now = datetime.now(timezone.utc)

                if healthy:
                    # Reset failure counter on success
                    await redis.delete(failure_key)

                    # Recovery: degraded -> active
                    if agent.status == AgentStatus.DEGRADED:
                        agent.status = AgentStatus.ACTIVE
                        await _publish_health_event(
                            agent, old_status, AgentStatus.ACTIVE, healthy=True
                        )
                        logger.info(
                            "agent_health_recovered",
                            agent_id=str(agent.agent_id),
                            agent_name=agent.name,
                        )

                    summary["healthy"] += 1
                else:
                    # Increment failure counter
                    failures = await redis.incr(failure_key)
                    # Set a TTL so stale keys don't accumulate (24h)
                    await redis.expire(failure_key, 86400)

                    if failures >= suspended_threshold and agent.status != AgentStatus.SUSPENDED:
                        # Suspend the agent
                        agent.status = AgentStatus.SUSPENDED
                        await _publish_health_event(
                            agent, old_status, AgentStatus.SUSPENDED, healthy=False
                        )
                        summary["suspended"] += 1
                        logger.warning(
                            "agent_health_suspended",
                            agent_id=str(agent.agent_id),
                            agent_name=agent.name,
                            consecutive_failures=failures,
                        )
                    elif failures >= degraded_threshold and agent.status == AgentStatus.ACTIVE:
                        # Degrade the agent
                        agent.status = AgentStatus.DEGRADED
                        await _publish_health_event(
                            agent, old_status, AgentStatus.DEGRADED, healthy=False
                        )
                        summary["degraded"] += 1
                        logger.warning(
                            "agent_health_degraded",
                            agent_id=str(agent.agent_id),
                            agent_name=agent.name,
                            consecutive_failures=failures,
                        )
                    else:
                        # Still within tolerance or already in the correct degraded state
                        if agent.status == AgentStatus.DEGRADED:
                            summary["degraded"] += 1
                        else:
                            summary["healthy"] += 1

                # Update last_health_check timestamp
                agent.last_health_check = now

        # Flush all changes
        await session.flush()

    logger.info("health_check_complete", **summary)
    return summary


async def _check_agent_health(client: httpx.AsyncClient, url: str) -> bool:
    """Perform an HTTP GET health check against an agent's health_check_url.

    Returns True if the agent responds with a 2xx status within the timeout.
    """
    try:
        response = await client.get(url)
        return 200 <= response.status_code < 300
    except (httpx.HTTPError, httpx.TimeoutException, Exception):
        return False


async def _publish_health_event(
    agent: Agent,
    old_status: AgentStatus,
    new_status: AgentStatus,
    healthy: bool,
) -> None:
    """Publish an AgentHealthEvent to the event bus."""
    event = AgentHealthEvent.create(
        agent_id=str(agent.agent_id),
        agent_name=agent.name,
        old_status=old_status.value,
        new_status=new_status.value,
        healthy=healthy,
    )
    await EventBus.publish(["agent", str(agent.agent_id), "health_changed"], event)
