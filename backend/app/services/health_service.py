"""System health aggregation service.

Provides on-demand single-agent health checks and a comprehensive
system health overview covering database, Redis, agents, memory,
workflows, trust events, embeddings, and WebSocket connections.
"""
from __future__ import annotations

import httpx
import redis.asyncio as aioredis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.models.memory import MemoryRecord
from app.models.trust import TrustEvent
from app.models.workflow import Workflow

logger = get_logger(__name__)


class HealthService:
    """Aggregates system-wide health information for the /health endpoint."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis
        self.settings = get_settings()

    async def check_agent(self, agent_id) -> dict:
        """On-demand health check for a single agent.

        Performs an HTTP GET to the agent's health_check_url and returns
        a dict with connectivity status, latency, and HTTP status code.
        """
        result = await self.session.execute(
            select(Agent).where(Agent.agent_id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return {"healthy": False, "error": "Agent not found"}

        if not agent.health_check_url:
            return {
                "agent_id": str(agent.agent_id),
                "agent_name": agent.name,
                "healthy": None,
                "error": "No health_check_url configured",
            }

        timeout = self.settings.health_check_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(agent.health_check_url)
                healthy = 200 <= response.status_code < 300
                return {
                    "agent_id": str(agent.agent_id),
                    "agent_name": agent.name,
                    "healthy": healthy,
                    "status_code": response.status_code,
                    "latency_ms": response.elapsed.total_seconds() * 1000,
                }
        except (httpx.HTTPError, httpx.TimeoutException, Exception) as exc:
            return {
                "agent_id": str(agent.agent_id),
                "agent_name": agent.name,
                "healthy": False,
                "error": str(exc),
            }

    async def get_system_health(self) -> dict:
        """Aggregate system health across all subsystems.

        Returns a comprehensive dict covering:
        - Database connectivity
        - Redis connectivity
        - Agent counts by status
        - Total memory records, workflows, trust events
        - Embedding service status
        - WebSocket connection count
        """
        health: dict = {
            "status": "healthy",
            "components": {},
        }

        # --- Database connectivity ---
        db_healthy = await self._check_db()
        health["components"]["database"] = {
            "healthy": db_healthy,
            "status": "up" if db_healthy else "down",
        }

        # --- Redis connectivity ---
        redis_healthy = await self._check_redis()
        health["components"]["redis"] = {
            "healthy": redis_healthy,
            "status": "up" if redis_healthy else "down",
        }

        # --- Agent counts by status ---
        agent_counts = await self._get_agent_counts()
        health["components"]["agents"] = agent_counts

        # --- Total memory records ---
        memory_count = await self._get_count(MemoryRecord)
        health["components"]["memory"] = {"total_records": memory_count}

        # --- Total workflows ---
        workflow_count = await self._get_count(Workflow)
        health["components"]["workflows"] = {"total_workflows": workflow_count}

        # --- Total trust events ---
        trust_count = await self._get_count(TrustEvent)
        health["components"]["trust"] = {"total_events": trust_count}

        # --- Embedding service status ---
        embedding_status = self._get_embedding_status()
        health["components"]["embeddings"] = embedding_status

        # --- WebSocket connections ---
        ws_status = self._get_ws_status()
        health["components"]["websocket"] = ws_status

        # Determine overall status
        if not db_healthy or not redis_healthy:
            health["status"] = "unhealthy"
        elif agent_counts.get("degraded", 0) > 0 or agent_counts.get("suspended", 0) > 0:
            health["status"] = "degraded"

        return health

    async def _check_db(self) -> bool:
        """Check database connectivity with SELECT 1."""
        try:
            result = await self.session.execute(text("SELECT 1"))
            return result.scalar_one() == 1
        except Exception:
            return False

    async def _check_redis(self) -> bool:
        """Check Redis connectivity with PING."""
        try:
            return await self.redis.ping()
        except Exception:
            return False

    async def _get_agent_counts(self) -> dict:
        """Get agent counts grouped by status."""
        result = await self.session.execute(
            select(Agent.status, func.count()).group_by(Agent.status)
        )
        rows = result.all()
        counts = {status.value: 0 for status in AgentStatus}
        for status, count in rows:
            key = status.value if isinstance(status, AgentStatus) else status
            counts[key] = count
        counts["total"] = sum(counts.values())
        return counts

    async def _get_count(self, model) -> int:
        """Get total count for a given model table."""
        try:
            result = await self.session.execute(
                select(func.count()).select_from(model)
            )
            return result.scalar_one()
        except Exception:
            return 0

    @staticmethod
    def _get_embedding_status() -> dict:
        """Get embedding service status without importing at module level."""
        try:
            from app.embeddings.service import get_embedding_service
            service = get_embedding_service()
            return service.get_status()
        except Exception:
            return {"loaded": False, "error": "Embedding service unavailable"}

    @staticmethod
    def _get_ws_status() -> dict:
        """Get WebSocket connection count from the connection manager."""
        try:
            from app.api.websocket import get_ws_manager
            manager = get_ws_manager()
            return manager.get_status()
        except Exception:
            return {"total_connections": 0, "error": "WebSocket manager unavailable"}
