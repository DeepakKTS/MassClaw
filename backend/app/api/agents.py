from __future__ import annotations

import uuid
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.dependencies import get_agent_service
from app.models.base import AgentStatus
from app.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentSearchParams,
    AgentSummary,
    AgentUpdate,
    HealthCheckResponse,
)
from app.schemas.common import PaginatedResponse, PaginationParams, SortParams
from app.services.agent_service import AgentService
from app.services.identity_service import IdentityService

router = APIRouter()


@router.post("", response_model=AgentResponse, status_code=201)
async def register_agent(
    data: AgentCreate,
    service: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Register a new agent in the MassClaw registry."""
    agent = await service.create_agent(data)
    return AgentResponse.model_validate(agent)


@router.get("", response_model=PaginatedResponse[AgentSummary])
async def list_agents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    capabilities: Annotated[list[str] | None, Query()] = None,
    status: AgentStatus | None = None,
    min_trust: float | None = Query(default=None, ge=0, le=1),
    max_cost: float | None = Query(default=None, ge=0),
    name_query: str | None = Query(default=None, max_length=255),
    service: AgentService = Depends(get_agent_service),
) -> PaginatedResponse[AgentSummary]:
    """List agents with pagination, sorting, and filtering."""
    pagination = PaginationParams(page=page, page_size=page_size)
    sort = SortParams(sort_by=sort_by, sort_order=sort_order)
    filters = AgentSearchParams(
        capabilities=capabilities,
        status=status,
        min_trust=min_trust,
        max_cost=max_cost,
        name_query=name_query,
    )

    result = await service.list_agents(pagination, sort, filters)

    return PaginatedResponse(
        items=[AgentSummary.model_validate(a) for a in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/search", response_model=list[AgentResponse])
async def search_agents(
    capabilities: Annotated[list[str] | None, Query()] = None,
    min_trust: float | None = Query(default=None, ge=0, le=1),
    max_cost: float | None = Query(default=None, ge=0),
    name_query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentResponse]:
    """Discovery query: find agents by capability with multi-factor ranking."""
    filters = AgentSearchParams(
        capabilities=capabilities,
        min_trust=min_trust,
        max_cost=max_cost,
        name_query=name_query,
    )
    agents = await service.search_agents(filters, limit=limit)
    return [AgentResponse.model_validate(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    service: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Get a single agent by ID."""
    agent = await service.get_agent(agent_id)
    return AgentResponse.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    service: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Update an agent's fields."""
    agent = await service.update_agent(agent_id, data)
    return AgentResponse.model_validate(agent)


@router.delete("/{agent_id}", status_code=204, response_model=None)
async def deactivate_agent(
    agent_id: uuid.UUID,
    service: AgentService = Depends(get_agent_service),
) -> None:
    """Soft-delete (deactivate) an agent."""
    await service.delete_agent(agent_id)


@router.post("/{agent_id}/health-check", response_model=HealthCheckResponse)
async def check_agent_health(
    agent_id: uuid.UUID,
    service: AgentService = Depends(get_agent_service),
) -> HealthCheckResponse:
    """Perform an on-demand health check on an agent."""
    return await service.health_check(agent_id)


# --------------------------------------------------------------------- AgentFacts


@router.get(
    "/{agent_id}/agent-facts.json",
    response_model=None,
    tags=["Identity"],
    summary="Signed AgentFacts document for this agent.",
)
async def get_agent_facts_document(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Return the NANDA-style AgentFacts document describing the agent.

    The document is signed by a keypair unique to the agent (generated on
    first access and persisted encrypted in the agent's metadata). A stock
    OpenClaw agent can call ``GET /api/v1/agents/{id}/agent-facts.json`` to
    learn the agent's capabilities, endpoint, and declared limits.
    """
    service = IdentityService(session=session, redis=redis)
    facts = await service.get_agent_facts(agent_id)
    return facts.to_document()


@router.post(
    "/verify-facts",
    tags=["Identity"],
    summary="Verify a posted AgentFacts document.",
)
async def verify_posted_agent_facts(
    document: dict[str, Any] = Body(..., description="Full AgentFacts JSON document to verify."),
) -> dict[str, Any]:
    """Verify the signature of an AgentFacts document without trusting the caller.

    Useful for front-end 'verify signature' buttons and for peer nodes handed a
    document by an unknown third party. No database state is read or written.
    """
    return IdentityService.verify_document(document)
