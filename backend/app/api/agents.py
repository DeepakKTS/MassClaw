from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

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
