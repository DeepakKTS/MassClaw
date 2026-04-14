"""Test fixtures for MassClaw backend."""

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, get_session_factory, init_db, dispose_db, get_engine
from app.core.redis import init_redis, dispose_redis, get_redis_manager
from app.models import *  # noqa: F401, F403 — ensure all models registered


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test database session with rollback.

    Disposes and re-creates the engine each time to ensure the connection pool
    is bound to the current event loop (prevents 'Future attached to a different
    loop' errors with function-scoped asyncio event loops).
    """
    await dispose_db()
    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def redis_client():
    """Per-test Redis client."""
    try:
        await dispose_redis()
    except Exception:
        pass
    await init_redis()
    client = get_redis_manager().get_cache_client()
    yield client


@pytest_asyncio.fixture
async def sample_agent(db_session):
    """Create a sample agent for testing."""
    from app.models.agent import Agent
    from app.models.base import AgentStatus

    agent = Agent(
        name=f"test-agent-{uuid.uuid4().hex[:8]}",
        description="Test agent for unit tests",
        capabilities=["test", "research", "analysis"],
        endpoint="internal://test",
        trust_score=0.75,
        status=AgentStatus.ACTIVE,
        cost_profile={"avg_cost_per_call": 0.01},
        latency_profile={"p50_ms": 500, "p95_ms": 2000},
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)
    return agent


@pytest_asyncio.fixture
async def sample_workflow(db_session):
    """Create a sample workflow for testing."""
    from app.models.workflow import Workflow

    wf = Workflow(
        user_id="test-user",
        prompt="Test workflow prompt for analysis",
        domain="test",
        budget_limit=500,
    )
    db_session.add(wf)
    await db_session.flush()
    await db_session.refresh(wf)
    return wf
