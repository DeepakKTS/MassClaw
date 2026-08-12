"""Integration tests for memory semantic search."""

import pytest
import pytest_asyncio

from app.services.memory_service import MemoryService
from app.schemas.memory import MemoryWriteRequest, MemoryQueryRequest
from app.models.base import MemoryType
from app.embeddings.service import init_embedding_service


class TestMemorySearch:
    @pytest_asyncio.fixture(autouse=True)
    async def setup_embeddings(self):
        await init_embedding_service()

    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return MemoryService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_write_and_query(self, service, sample_workflow):
        # Write
        record = await service.write_memory(
            MemoryWriteRequest(
                workflow_id=sample_workflow.workflow_id,
                memory_type=MemoryType.RESULT,
                content="The hospital registration desk has a 35-minute average wait time.",
                confidence=0.9,
            )
        )
        assert record.memory_id is not None
        assert record.embedding is not None

        # Query
        results = await service.query_memory(
            MemoryQueryRequest(
                query="patient wait times at registration",
                workflow_id=sample_workflow.workflow_id,
                min_similarity=0.2,
                top_k=5,
            )
        )
        assert len(results) >= 1
        assert results[0].similarity > 0.3

    @pytest.mark.asyncio
    async def test_version_chain(self, service, sample_workflow):
        v1 = await service.write_memory(
            MemoryWriteRequest(
                workflow_id=sample_workflow.workflow_id,
                memory_type=MemoryType.RESULT,
                content="Initial analysis of bottleneck areas.",
                confidence=0.8,
            )
        )
        v2 = await service.write_memory(
            MemoryWriteRequest(
                workflow_id=sample_workflow.workflow_id,
                memory_type=MemoryType.RESULT,
                content="Updated analysis with additional data points.",
                confidence=0.9,
                parent_version_id=v1.memory_id,
            )
        )
        assert v2.version == 2
        versions = await service.get_memory_versions(v1.memory_id)
        assert len(versions) >= 2

    @pytest.mark.asyncio
    async def test_soft_delete(self, service, sample_workflow):
        record = await service.write_memory(
            MemoryWriteRequest(
                workflow_id=sample_workflow.workflow_id,
                memory_type=MemoryType.CONTEXT,
                content="Temporary context that will be deleted.",
                confidence=0.85,
            )
        )
        await service.delete_memory(record.memory_id)

        results = await service.query_memory(
            MemoryQueryRequest(
                query="temporary context deleted",
                workflow_id=sample_workflow.workflow_id,
                min_similarity=0.1,
                min_confidence=0.01,
                top_k=10,
            )
        )
        # Deleted record (confidence=0) should be excluded
        assert not any(r.memory.memory_id == record.memory_id for r in results)


class TestApproximateSearchRecall:
    """Guards against pgvector's default ``ivfflat.probes = 1``.

    The embedding index is built with ``lists = 100``. At probes=1 an
    approximate search scans a single list, so on a small table the one
    matching row is very unlikely to be in the list that gets scanned — the
    query comes back empty even for a near-identical record. Measured on a
    freshly created database: a record with cosine similarity 0.75 to the query
    was invisible at probes=1 and found at probes=100.

    That is a correctness failure for shared agent memory (a fresh node would
    answer "nothing known" about something it had just written), and it made
    this file order-dependent — it only passed once earlier tests had put enough
    embeddings in the table.
    """

    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return MemoryService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_single_record_is_findable_in_an_otherwise_empty_table(self, service, sample_workflow):
        """The minimal case the old default got wrong."""
        await service.write_memory(
            MemoryWriteRequest(
                workflow_id=sample_workflow.workflow_id,
                memory_type=MemoryType.RESULT,
                content="Ambulance turnaround at the trauma bay averages 12 minutes.",
                confidence=0.9,
            )
        )

        results = await service.query_memory(
            MemoryQueryRequest(
                query="how long do ambulances take to turn around",
                workflow_id=sample_workflow.workflow_id,
                min_similarity=0.2,
                top_k=5,
            )
        )

        assert len(results) == 1, "a lone strongly-matching record must not be lost to ANN recall"
        assert results[0].similarity > 0.3

    @pytest.mark.asyncio
    async def test_probes_are_configured_above_the_pgvector_default(self):
        from app.config import get_settings

        assert get_settings().memory_ivfflat_probes > 1
