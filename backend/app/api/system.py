from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.cache_metrics import semantic_cache_snapshot
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

    # Agent and workflow counts, one GROUP BY each.
    #
    # These were a query per status value — every AgentStatus member plus four
    # WorkflowStatus members, so ten sequential round-trips before the rest of
    # the endpoint even started. It matters more than the count suggests: this
    # is the most-polled endpoint in the app, hit independently by TopBar,
    # QuickStats and DashboardStats, so an idle dashboard was issuing three
    # copies of the whole storm on a timer.
    agent_rows = await session.execute(select(Agent.status, func.count()).group_by(Agent.status))
    counted_agents = {status.value: count for status, count in agent_rows}
    # Statuses with no rows are absent from a GROUP BY, so seed every member to
    # keep the response shape stable for clients.
    agent_counts = {status.value: counted_agents.get(status.value, 0) for status in AgentStatus}

    workflow_rows = await session.execute(select(Workflow.status, func.count()).group_by(Workflow.status))
    counted_workflows = {status.value: count for status, count in workflow_rows}
    workflow_counts = {
        status.value: counted_workflows.get(status.value, 0)
        for status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.RUNNING,
            WorkflowStatus.PENDING,
        )
    }

    # Task counts
    task_result = await session.execute(
        select(
            func.count().label("total"),
            func.count().filter(Task.status == TaskStatus.COMPLETED).label("completed"),
            func.count().filter(Task.status == TaskStatus.FAILED).label("failed"),
        ).select_from(Task)
    )
    task_counts = task_result.one()

    # Table totals in one round-trip instead of three. Each is still a full
    # count — on Postgres that is a sequential scan, so memory_records in
    # particular gets slower as the CRDT store grows. Worth revisiting with an
    # approximate count from pg_class.reltuples if this endpoint stays this hot.
    totals = (
        await session.execute(
            select(
                select(func.count()).select_from(MemoryRecord).scalar_subquery().label("memory"),
                select(func.count()).select_from(TrustEvent).scalar_subquery().label("trust"),
                select(func.count()).select_from(WalletEvent).scalar_subquery().label("wallet"),
            )
        )
    ).one()
    memory_count = totals.memory
    trust_count = totals.trust
    wallet_count = totals.wallet

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

    # Semantic result cache. A hit costs zero credits and 0.5ms, so it is
    # otherwise indistinguishable from a task that was simply cheap — these
    # counters are the only way to tell a working cache from a dead one, or to
    # explain a drop in spend.
    cache_counters = await semantic_cache_snapshot(redis)

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
        "semantic_cache": cache_counters,
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
