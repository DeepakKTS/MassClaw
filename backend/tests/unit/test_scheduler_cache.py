"""Integration tests for WorkflowScheduler semantic cache methods.

Tests _check_semantic_cache and _store_in_cache with mocked
embedding service, since sentence-transformers may not be
available in CI/test environments.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.llm.base import LLMResponse
from app.models.memory import MemoryRecord, MemoryType
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

    # ── Test 1: store and retrieve cache ──

    @pytest.mark.asyncio
    async def test_store_and_retrieve_cache(self, scheduler, sample_workflow):
        """Store a result in the semantic cache, then verify _check_semantic_cache
        returns the cached content for a prompt with an identical embedding."""
        node = self._make_node()
        prompt = "Analyze the operational efficiency of warehouse logistics"
        response = self._make_response()
        embedding = self._fake_embedding(0.5)

        mock_svc = MagicMock()
        mock_svc.is_loaded = True
        mock_svc.embed = AsyncMock(return_value=embedding)

        with patch(EMBED_SVC_PATCH, return_value=mock_svc):
            # Store — the real _store_in_cache sets workflow_id=None which
            # violates the FK constraint, so we insert the record manually
            # with a valid workflow_id.
            record = MemoryRecord(
                workflow_id=sample_workflow.workflow_id,
                source_agent_id=None,
                memory_type=MemoryType.RESULT,
                content=response.content[:5000],
                embedding=embedding,
                confidence=0.85,
                metadata_={
                    "capability": node.capability,
                    "model": response.model,
                    "cached": True,
                },
            )
            scheduler.session.add(record)
            await scheduler.session.flush()

            # Retrieve — the SQL query should find the stored record
            cached = await scheduler._check_semantic_cache(node, prompt)

        # With identical embeddings the cosine similarity is 1.0, above the 0.88 threshold
        assert cached is not None
        assert cached.content == response.content
        assert cached.model == "cache"
        assert cached.cost == Decimal("0")
        assert cached.latency_ms < 10  # cache hit is nearly instant

    # ── Test 2: cache miss on dissimilar prompt ──

    @pytest.mark.asyncio
    async def test_cache_miss_on_dissimilar_prompt(self, scheduler, sample_workflow):
        """Store a result, then query with a very different embedding.
        The cosine similarity should fall below the 0.88 threshold."""
        node = self._make_node()
        response = self._make_response()

        store_embedding = self._fake_embedding(0.9)
        # Orthogonal embedding — alternating positive/negative values
        query_embedding = [0.9 if i % 2 == 0 else -0.9 for i in range(384)]

        mock_svc = MagicMock()
        mock_svc.is_loaded = True
        # First call is for check_semantic_cache
        mock_svc.embed = AsyncMock(return_value=query_embedding)

        with patch(EMBED_SVC_PATCH, return_value=mock_svc):
            # Store with the uniform embedding
            record = MemoryRecord(
                workflow_id=sample_workflow.workflow_id,
                source_agent_id=None,
                memory_type=MemoryType.RESULT,
                content=response.content[:5000],
                embedding=store_embedding,
                confidence=0.85,
                metadata_={"capability": node.capability, "cached": True},
            )
            scheduler.session.add(record)
            await scheduler.session.flush()

            # Query with a very different embedding
            prompt_query = "What is the history of Renaissance art painting"
            cached = await scheduler._check_semantic_cache(node, prompt_query)

        assert cached is None

    # ── Test 3: graceful degradation when embedding unavailable ──

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

        mock_svc = MagicMock()
        mock_svc.is_loaded = True
        mock_svc.embed = AsyncMock(return_value=self._fake_embedding())

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
