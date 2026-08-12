"""Integration tests for WorkflowScheduler semantic cache methods.

Tests _check_semantic_cache and _store_in_cache with mocked
embedding service, since sentence-transformers may not be
available in CI/test environments.

The cache is a *distinct class* of memory record, not a high-confidence
``result`` row: it is keyed by capability, expires, and is re-checked
against the poison markers on the way out. Every test below pins one
edge of that contract — the read path must refuse anything it cannot
positively identify as a live cache entry for this capability.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import get_settings
from app.llm.base import LLMResponse
from app.models.base import MemoryType
from app.models.memory import MemoryRecord
from app.orchestration.dag import DAGNode
from app.orchestration.scheduler import WorkflowScheduler

# Patch target: the function is imported inside method bodies via
#   from app.embeddings.service import get_embedding_service
# so we patch it at source.
EMBED_SVC_PATCH = "app.embeddings.service.get_embedding_service"


class TestSchedulerSemanticCache:
    """Tests for the semantic result cache on WorkflowScheduler."""

    @pytest_asyncio.fixture
    async def scheduler(self, db_session, redis_client):
        return WorkflowScheduler(db_session, redis_client)

    @staticmethod
    def _make_node(capability: str = "research", description: str = "Analyze data") -> DAGNode:
        return DAGNode(
            node_id="step-1",
            capability=capability,
            description=description,
        )

    @staticmethod
    def _make_response(
        content: str = "This is a detailed research result with enough content to pass the 50-char minimum.",
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

    @staticmethod
    def _fake_embedding(seed: float = 0.1) -> list[float]:
        """Return a deterministic 384-dim embedding for testing."""
        return [seed] * 384

    @staticmethod
    def _mock_embedder(embedding: list[float]) -> MagicMock:
        svc = MagicMock()
        svc.is_loaded = True
        svc.embed = AsyncMock(return_value=embedding)
        return svc

    async def _stored_cache_row(self, scheduler) -> MemoryRecord:
        """Return the single cache row written by _store_in_cache."""
        rows = (
            (await scheduler.session.execute(select(MemoryRecord).order_by(MemoryRecord.created_at.desc()).limit(1)))
            .scalars()
            .all()
        )
        assert rows, "expected _store_in_cache to have written a row"
        return rows[0]

    # ── Test 1: store and retrieve cache ──

    @pytest.mark.asyncio
    async def test_store_and_retrieve_cache(self, scheduler):
        """A stored entry is served back for a prompt with the same embedding."""
        node = self._make_node()
        prompt = "Analyze the operational efficiency of warehouse logistics"
        response = self._make_response()

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.5))):
            await scheduler._store_in_cache(node, prompt, response)
            cached = await scheduler._check_semantic_cache(node, prompt)

        # With identical embeddings the cosine similarity is 1.0, above the 0.88 threshold
        assert cached is not None
        assert cached.content == response.content
        assert cached.model == "cache"
        assert cached.cost == Decimal("0")
        assert cached.latency_ms < 10  # cache hit is nearly instant

    # ── Test 2: cache miss on dissimilar prompt ──

    @pytest.mark.asyncio
    async def test_cache_miss_on_dissimilar_prompt(self, scheduler):
        """A very different embedding falls below the 0.88 similarity threshold."""
        node = self._make_node()
        response = self._make_response()

        # Orthogonal embedding — alternating positive/negative values
        query_embedding = [0.9 if i % 2 == 0 else -0.9 for i in range(384)]

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.9))):
            await scheduler._store_in_cache(node, "Warehouse logistics efficiency", response)

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(query_embedding)):
            cached = await scheduler._check_semantic_cache(node, "What is the history of Renaissance art painting")

        assert cached is None

    # ── Test 3: a genuine task result must never be served as a cache hit ──

    @pytest.mark.asyncio
    async def test_real_task_result_is_not_served_as_cache_hit(self, scheduler, sample_workflow):
        """Task outputs are written at confidence 0.85 with memory_type=result —
        exactly what the cache read used to filter on. Serving one would hand
        another workflow's private output to an unrelated caller."""
        node = self._make_node()
        embedding = self._fake_embedding(0.4)

        scheduler.session.add(
            MemoryRecord(
                workflow_id=sample_workflow.workflow_id,
                source_agent_id=None,
                memory_type=MemoryType.RESULT,
                content="Confidential Q3 revenue figures for the acquisition target, at length.",
                embedding=embedding,
                confidence=0.85,
                metadata_={"capability": node.capability, "task_id": "abc"},
            )
        )
        await scheduler.session.flush()

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(embedding)):
            cached = await scheduler._check_semantic_cache(node, "Any prompt at all")

        assert cached is None

    # ── Test 4: entries are scoped to the capability that produced them ──

    @pytest.mark.asyncio
    async def test_cache_hit_requires_matching_capability(self, scheduler):
        """An entry produced by a research node must not answer a code node."""
        producer = self._make_node(capability="research")
        consumer = self._make_node(capability="code_generation")
        prompt = "Summarise the deployment topology"

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.3))):
            await scheduler._store_in_cache(producer, prompt, self._make_response())
            cached = await scheduler._check_semantic_cache(consumer, prompt)

        assert cached is None

    # ── Test 5: expiry is enforced on read ──

    @pytest.mark.asyncio
    async def test_expired_entry_is_not_served(self, scheduler):
        """Past expires_at means the row is dead even before GC collects it."""
        node = self._make_node()
        prompt = "Analyze warehouse throughput"

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.6))):
            await scheduler._store_in_cache(node, prompt, self._make_response())
            row = await self._stored_cache_row(scheduler)
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await scheduler.session.flush()

            cached = await scheduler._check_semantic_cache(node, prompt)

        assert cached is None

    # ── Test 6: lifecycle state is enforced on read ──

    @pytest.mark.asyncio
    async def test_tombstoned_entry_is_not_served(self, scheduler):
        """GC tombstones an entry before hard-deleting it; it must stop serving
        the moment it is tombstoned, not when the row finally disappears."""
        from app.models.base import RecordState

        node = self._make_node()
        prompt = "Analyze warehouse throughput"

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.7))):
            await scheduler._store_in_cache(node, prompt, self._make_response())
            row = await self._stored_cache_row(scheduler)
            row.record_state = RecordState.TOMBSTONED
            await scheduler.session.flush()

            cached = await scheduler._check_semantic_cache(node, prompt)

        assert cached is None

    # ── Test 7: the guard version invalidates entries wholesale ──

    @pytest.mark.asyncio
    async def test_entry_from_an_older_guard_version_is_not_served(self, scheduler):
        """Bumping the guard version must retire every entry written under the
        old one — otherwise a widened poison-marker list can never take effect
        on rows already in the table."""
        node = self._make_node()
        prompt = "Analyze warehouse throughput"

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.8))):
            await scheduler._store_in_cache(node, prompt, self._make_response())
            row = await self._stored_cache_row(scheduler)
            row.metadata_ = {**row.metadata_, "cache_version": 0}
            await scheduler.session.flush()

            cached = await scheduler._check_semantic_cache(node, prompt)

        assert cached is None

    # ── Test 8: the poison guard also runs on the way out ──

    @pytest.mark.asyncio
    async def test_poisoned_content_is_not_served(self, scheduler):
        """Rows that predate the write-side guard (or slipped past it) must be
        rejected on read, so a stale failure cannot masquerade as a 0.5ms
        success forever."""
        node = self._make_node()
        prompt = "Analyze warehouse throughput"

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.55))):
            await scheduler._store_in_cache(node, prompt, self._make_response())
            row = await self._stored_cache_row(scheduler)
            row.content = "The requested tool not found in this environment, so the task could not run."
            await scheduler.session.flush()

            cached = await scheduler._check_semantic_cache(node, prompt)

        assert cached is None

    # ── Test 9: the write contract ──

    @pytest.mark.asyncio
    async def test_store_writes_a_distinguishable_expiring_row(self, scheduler):
        """Cache writes must be their own memory class, cross-workflow, with an
        expiry GC can act on and the metadata the read path filters by."""
        node = self._make_node()
        response = self._make_response()

        with patch(EMBED_SVC_PATCH, return_value=self._mock_embedder(self._fake_embedding(0.2))):
            await scheduler._store_in_cache(node, "Analyze warehouse throughput", response)

        row = await self._stored_cache_row(scheduler)
        assert row.memory_type.value == "cache"
        assert row.workflow_id is None
        assert row.metadata_["capability"] == node.capability
        assert row.metadata_["cache_version"] == WorkflowScheduler._CACHE_GUARD_VERSION
        assert row.expires_at is not None
        expected = datetime.now(UTC) + timedelta(hours=get_settings().semantic_cache_ttl_hours)
        assert abs((row.expires_at - expected).total_seconds()) < 300

    # ── Test 10: graceful degradation when embedding unavailable ──

    @pytest.mark.asyncio
    async def test_cache_skip_when_embedding_unavailable(self, scheduler):
        """Both methods should gracefully return None when the embedding
        service is not loaded (no crash, no exception propagated)."""
        node = self._make_node()
        prompt = "Some prompt that won't matter"
        response = self._make_response()

        mock_svc = MagicMock()
        mock_svc.is_loaded = False

        with patch(EMBED_SVC_PATCH, return_value=mock_svc):
            cached = await scheduler._check_semantic_cache(node, prompt)
            assert cached is None

            # _store_in_cache should also silently return without error
            await scheduler._store_in_cache(node, prompt, response)
            # No exception means success — nothing stored

    @pytest.mark.asyncio
    async def test_cache_skip_when_response_too_short(self, scheduler):
        """_store_in_cache should skip caching for responses shorter than 50 chars."""
        node = self._make_node()
        prompt = "Some prompt"
        short_response = self._make_response(content="Short")

        mock_svc = self._mock_embedder(self._fake_embedding())

        with patch(EMBED_SVC_PATCH, return_value=mock_svc):
            await scheduler._store_in_cache(node, prompt, short_response)
            # embed should never be called because the short-content guard fires first
            mock_svc.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_check_handles_db_error_gracefully(self, scheduler):
        """If the embedding call fails, _check_semantic_cache should return None
        rather than raising an exception."""
        node = self._make_node()
        prompt = "Any prompt"

        mock_svc = MagicMock()
        mock_svc.is_loaded = True
        mock_svc.embed = AsyncMock(side_effect=RuntimeError("embedding model crashed"))

        with patch(EMBED_SVC_PATCH, return_value=mock_svc):
            cached = await scheduler._check_semantic_cache(node, prompt)
            assert cached is None
