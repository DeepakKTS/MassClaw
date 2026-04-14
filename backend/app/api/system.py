from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import get_db_session, get_engine
from app.core.redis import get_redis
from app.embeddings.service import get_embedding_service

router = APIRouter()


@router.get("/metrics")
async def prometheus_metrics(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Prometheus-compatible metrics endpoint.

    Returns key system metrics for monitoring dashboards.
    """
    from sqlalchemy import func, select

    from app.models.agent import Agent
    from app.models.base import AgentStatus, TaskStatus, WorkflowStatus
    from app.models.memory import MemoryRecord
    from app.models.task import Task
    from app.models.trust import TrustEvent
    from app.models.wallet import WalletEvent
    from app.models.workflow import Workflow

    # Agent counts by status
    agent_counts = {}
    for status in AgentStatus:
        result = await session.execute(select(func.count()).select_from(Agent).where(Agent.status == status))
        agent_counts[status.value] = result.scalar_one()

    # Workflow counts by status
    workflow_counts = {}
    for status in [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.RUNNING, WorkflowStatus.PENDING]:
        result = await session.execute(select(func.count()).select_from(Workflow).where(Workflow.status == status))
        workflow_counts[status.value] = result.scalar_one()

    # Task counts
    task_result = await session.execute(
        select(
            func.count().label("total"),
            func.count().filter(Task.status == TaskStatus.COMPLETED).label("completed"),
            func.count().filter(Task.status == TaskStatus.FAILED).label("failed"),
        ).select_from(Task)
    )
    task_counts = task_result.one()

    # Total counts
    memory_count = (await session.execute(select(func.count()).select_from(MemoryRecord))).scalar_one()
    trust_count = (await session.execute(select(func.count()).select_from(TrustEvent))).scalar_one()
    wallet_count = (await session.execute(select(func.count()).select_from(WalletEvent))).scalar_one()

    # DB pool status
    engine = get_engine()
    pool = engine.pool
    pool_status = {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }

    # Redis info
    redis_info = await redis.info("memory")
    redis_memory = {
        "used_memory_mb": round(redis_info.get("used_memory", 0) / 1024 / 1024, 2),
        "used_memory_peak_mb": round(redis_info.get("used_memory_peak", 0) / 1024 / 1024, 2),
    }

    # Embedding service
    emb = get_embedding_service()
    embedding_status = emb.get_status()

    # WebSocket connections
    try:
        from app.api.websocket import get_ws_manager

        ws_status = get_ws_manager().get_status()
    except Exception:
        ws_status = {"total_connections": 0}

    return {
        "agents": agent_counts,
        "workflows": workflow_counts,
        "tasks": {
            "total": task_counts.total,
            "completed": task_counts.completed,
            "failed": task_counts.failed,
        },
        "memory_records": memory_count,
        "trust_events": trust_count,
        "wallet_events": wallet_count,
        "database_pool": pool_status,
        "redis_memory": redis_memory,
        "embedding_service": embedding_status,
        "websocket": ws_status,
    }


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Return non-sensitive configuration for diagnostics."""
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "version": settings.app_version,
        "log_level": settings.log_level,
        "embedding_model": settings.embedding_model,
        "trust_weights": settings.trust_score_weights,
        "trust_decay_rate": settings.trust_decay_rate,
        "memory_freshness_lambda": settings.memory_freshness_decay_lambda,
        "memory_min_similarity": settings.memory_min_similarity_threshold,
        "rate_limit": f"{settings.rate_limit_requests}/{settings.rate_limit_window_seconds}s",
        "health_check_interval": f"{settings.health_check_interval_seconds}s",
    }
