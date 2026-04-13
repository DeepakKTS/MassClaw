from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_trust_service
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.trust import (
    TrustBreakdown,
    TrustEventResponse,
    TrustScoreInput,
    TrustSummary,
)
from app.services.trust_service import TrustService

router = APIRouter()


@router.get("/{agent_id}", response_model=TrustBreakdown)
async def get_trust_breakdown(
    agent_id: uuid.UUID,
    service: TrustService = Depends(get_trust_service),
) -> TrustBreakdown:
    """Get current trust score with full dimension breakdown for an agent."""
    return await service.get_trust_breakdown(agent_id)


@router.get("/{agent_id}/history", response_model=PaginatedResponse[TrustEventResponse])
async def get_trust_history(
    agent_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    since: datetime | None = Query(default=None, description="Filter events since this timestamp"),
    service: TrustService = Depends(get_trust_service),
) -> PaginatedResponse[TrustEventResponse]:
    """Get paginated trust event history for an agent."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.get_trust_history(agent_id, pagination, since=since)


@router.get("/leaderboard/ranked", response_model=list[TrustSummary])
async def get_trust_leaderboard(
    limit: int = Query(default=20, ge=1, le=100),
    capabilities: Annotated[list[str] | None, Query()] = None,
    service: TrustService = Depends(get_trust_service),
) -> list[TrustSummary]:
    """Get agents ranked by trust score."""
    return await service.get_leaderboard(limit=limit, capability_filter=capabilities)


@router.post("/{agent_id}/record", response_model=TrustEventResponse, status_code=201)
async def record_trust_event(
    agent_id: uuid.UUID,
    scores: TrustScoreInput,
    workflow_id: uuid.UUID | None = Query(default=None),
    task_id: uuid.UUID | None = Query(default=None),
    service: TrustService = Depends(get_trust_service),
) -> TrustEventResponse:
    """Record a trust event and update agent trust score.

    This is typically called by the orchestrator after task completion,
    but exposed for testing and manual adjustments.
    """
    event = await service.record_trust_event(
        agent_id=agent_id,
        scores=scores,
        workflow_id=workflow_id,
        task_id=task_id,
    )
    return TrustEventResponse.model_validate(event)
