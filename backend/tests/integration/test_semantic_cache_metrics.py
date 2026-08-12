"""Semantic cache hit/miss telemetry.

A cache hit returns in 0.5ms at zero cost, which is indistinguishable in the
workflow record from a task that was simply cheap — the reservation is settled
at zero credits and the run looks free. Without counters the only evidence a
cache exists at all is a log line, so nobody can answer "is it working?" or
"why did cost drop?". These pin the counters and their exposure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.cache_metrics import CACHE_METRIC_KEYS, semantic_cache_snapshot
from app.core.database import get_db_session
from app.core.redis import get_redis
from app.llm.base import LLMResponse
from app.main import app
from app.orchestration.dag import DAGNode
from app.orchestration.scheduler import WorkflowScheduler

EMBED_SVC_PATCH = "app.embeddings.service.get_embedding_service"


def _node(capability: str = "research") -> DAGNode:
    return DAGNode(node_id="step-1", capability=capability, description="Analyze data")


def _response(
    content: str = "A detailed research result with enough content to clear the 50-char floor.",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="claude-sonnet-4-20250514",
        input_tokens=200,
        output_tokens=300,
        cost=Decimal("0.005"),
        latency_ms=1200.0,
        metadata={"stop_reason": "end_turn", "provider": "anthropic"},
    )


def _embedder(embedding: list[float]) -> MagicMock:
    svc = MagicMock()
    svc.is_loaded = True
    svc.embed = AsyncMock(return_value=embedding)
    return svc


@pytest_asyncio.fixture
async def scheduler(db_session, redis_client) -> WorkflowScheduler:
    # Counters are process-wide by design, so clear them per test.
    await redis_client.delete(*CACHE_METRIC_KEYS.values())
    return WorkflowScheduler(db_session, redis_client)


class TestSemanticCacheCounters:
    @pytest.mark.asyncio
    async def test_hit_increments_the_hit_counter(self, scheduler, redis_client):
        node = _node()
        prompt = "Analyze the operational efficiency of warehouse logistics"

        with patch(EMBED_SVC_PATCH, return_value=_embedder([0.5] * 384)):
            await scheduler._store_in_cache(node, prompt, _response())
            assert await scheduler._check_semantic_cache(node, prompt) is not None

        snapshot = await semantic_cache_snapshot(redis_client)
        assert snapshot["hits"] == 1
        assert snapshot["misses"] == 0

    @pytest.mark.asyncio
    async def test_miss_increments_the_miss_counter(self, scheduler, redis_client):
        node = _node()

        with patch(EMBED_SVC_PATCH, return_value=_embedder([0.5] * 384)):
            await scheduler._store_in_cache(node, "Warehouse logistics", _response())

        orthogonal = [0.9 if i % 2 == 0 else -0.9 for i in range(384)]
        with patch(EMBED_SVC_PATCH, return_value=_embedder(orthogonal)):
            assert await scheduler._check_semantic_cache(node, "Renaissance art history") is None

        snapshot = await semantic_cache_snapshot(redis_client)
        assert snapshot["misses"] == 1
        assert snapshot["hits"] == 0

    @pytest.mark.asyncio
    async def test_rejected_entry_counts_as_both_a_miss_and_a_rejection(self, scheduler, redis_client):
        """The caller pays for an LLM call either way, so it is a miss — but a
        rejection is worth counting separately: a climbing number means entries
        are being written that should never have been."""
        node = _node()
        prompt = "Analyze warehouse throughput"

        with patch(EMBED_SVC_PATCH, return_value=_embedder([0.45] * 384)):
            await scheduler._store_in_cache(node, prompt, _response())
            rows = await scheduler.session.execute(
                text("UPDATE memory_records SET content = :c WHERE memory_type = 'cache'"),
                {"c": "The requested tool not found in this environment, so nothing ran."},
            )
            assert rows.rowcount >= 1
            assert await scheduler._check_semantic_cache(node, prompt) is None

        snapshot = await semantic_cache_snapshot(redis_client)
        assert snapshot["rejected"] == 1
        assert snapshot["misses"] == 1

    @pytest.mark.asyncio
    async def test_store_increments_the_store_counter(self, scheduler, redis_client):
        with patch(EMBED_SVC_PATCH, return_value=_embedder([0.35] * 384)):
            await scheduler._store_in_cache(_node(), "Analyze throughput", _response())

        snapshot = await semantic_cache_snapshot(redis_client)
        assert snapshot["stores"] == 1

    @pytest.mark.asyncio
    async def test_hit_rate_is_reported_and_is_zero_before_any_lookup(self, scheduler, redis_client):
        empty = await semantic_cache_snapshot(redis_client)
        assert empty["hit_rate"] == 0.0

        node = _node()
        prompt = "Analyze the operational efficiency of warehouse logistics"
        with patch(EMBED_SVC_PATCH, return_value=_embedder([0.25] * 384)):
            await scheduler._store_in_cache(node, prompt, _response())
            await scheduler._check_semantic_cache(node, prompt)
            await scheduler._check_semantic_cache(node, prompt)
        orthogonal = [0.9 if i % 2 == 0 else -0.9 for i in range(384)]
        with patch(EMBED_SVC_PATCH, return_value=_embedder(orthogonal)):
            await scheduler._check_semantic_cache(node, "Something else entirely")

        snapshot = await semantic_cache_snapshot(redis_client)
        assert snapshot["hits"] == 2
        assert snapshot["misses"] == 1
        assert snapshot["hit_rate"] == pytest.approx(2 / 3, abs=0.001)

    @pytest.mark.asyncio
    async def test_counter_failure_never_breaks_a_lookup(self, scheduler):
        """Telemetry is not worth a failed workflow: if Redis is unreachable the
        lookup still has to answer."""
        node = _node()
        prompt = "Analyze the operational efficiency of warehouse logistics"

        with patch(EMBED_SVC_PATCH, return_value=_embedder([0.15] * 384)):
            await scheduler._store_in_cache(node, prompt, _response())
            with patch.object(scheduler.redis, "incr", AsyncMock(side_effect=RuntimeError("redis down"))):
                assert await scheduler._check_semantic_cache(node, prompt) is not None


class TestMetricsEndpoint:
    @pytest_asyncio.fixture
    async def client(self, db_session, redis_client) -> AsyncIterator[httpx.AsyncClient]:
        async def _db_override() -> AsyncIterator:
            yield db_session

        async def _redis_override() -> AsyncIterator:
            yield redis_client

        app.dependency_overrides[get_db_session] = _db_override
        app.dependency_overrides[get_redis] = _redis_override
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://massclaw.test") as ac:
                yield ac
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_metrics_exposes_the_semantic_cache_counters(self, client, redis_client):
        await redis_client.delete(*CACHE_METRIC_KEYS.values())
        await redis_client.incr(CACHE_METRIC_KEYS["hits"])
        await redis_client.incr(CACHE_METRIC_KEYS["hits"])
        await redis_client.incr(CACHE_METRIC_KEYS["misses"])

        response = await client.get("/system/metrics")

        assert response.status_code == 200
        cache = response.json()["semantic_cache"]
        assert cache["hits"] == 2
        assert cache["misses"] == 1
        assert cache["hit_rate"] == pytest.approx(2 / 3, abs=0.001)
