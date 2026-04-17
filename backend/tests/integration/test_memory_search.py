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
