"""Integration tests for agent CRUD operations."""

import pytest
import pytest_asyncio
import uuid

from app.services.agent_service import AgentService
from app.schemas.agent import AgentCreate, AgentUpdate, AgentSearchParams
from app.schemas.common import PaginationParams, SortParams
from app.exceptions import NotFoundError, ConflictError


class TestAgentCRUD:
    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return AgentService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_create_agent(self, service):
        data = AgentCreate(
            name=f"test-{uuid.uuid4().hex[:8]}",
            description="Integration test agent",
            capabilities=["test", "research"],
            endpoint="internal://test",
        )
        agent = await service.create_agent(data)
        assert agent.agent_id is not None
        assert agent.name == data.name
        assert agent.trust_score == 0.5

    @pytest.mark.asyncio
    async def test_get_agent(self, service, sample_agent):
        agent = await service.get_agent(sample_agent.agent_id)
        assert agent.name == sample_agent.name

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, service):
        with pytest.raises(NotFoundError):
            await service.get_agent(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_duplicate_name_conflict(self, service, sample_agent):
        data = AgentCreate(
            name=sample_agent.name,
            description="Duplicate name test",
            capabilities=["test"],
            endpoint="internal://dup",
        )
        with pytest.raises(ConflictError):
            await service.create_agent(data)

    @pytest.mark.asyncio
    async def test_update_agent(self, service, sample_agent):
        updated = await service.update_agent(
            sample_agent.agent_id,
            AgentUpdate(safety_level=5),
        )
        assert updated.safety_level == 5

    @pytest.mark.asyncio
    async def test_delete_agent(self, service, sample_agent):
        await service.delete_agent(sample_agent.agent_id)
        agent = await service.get_agent(sample_agent.agent_id)
        assert agent.status.value == "inactive"

    @pytest.mark.asyncio
    async def test_list_agents_paginated(self, service, sample_agent):
        result = await service.list_agents(
            PaginationParams(page=1, page_size=10),
            SortParams(sort_by="created_at", sort_order="desc"),
        )
        assert result.total >= 1
        assert len(result.items) >= 1

    @pytest.mark.asyncio
    async def test_search_by_capability(self, service, sample_agent):
        results = await service.search_agents(
            AgentSearchParams(capabilities=["test"]),
        )
        assert any(a.agent_id == sample_agent.agent_id for a in results)
