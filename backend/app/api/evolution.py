from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_evolution_service
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.score import (
    AgentEvolution,
    AgentRanking,
    AgentScoreResponse,
    ScoreInput,
)
from app.services.evolution_service import EvolutionService

router = APIRouter()


@router.get("/{agent_id}", response_model=AgentEvolution)
async def get_agent_evolution(
    agent_id: uuid.UUID,
    service: EvolutionService = Depends(get_evolution_service),
) -> AgentEvolution:
    """Get comprehensive evolution data: current scores, percentile, trend, dimensions."""
    return await service.get_agent_evolution(agent_id)


@router.get("/{agent_id}/history", response_model=PaginatedResponse[AgentScoreResponse])
async def get_score_history(
    agent_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: EvolutionService = Depends(get_evolution_service),
) -> PaginatedResponse[AgentScoreResponse]:
    """Get paginated score history for an agent."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.get_score_history(agent_id, pagination)


@router.get("/rankings/leaderboard", response_model=list[AgentRanking])
async def get_rankings(
    limit: int = Query(default=20, ge=1, le=100),
    capability: str | None = Query(default=None),
    service: EvolutionService = Depends(get_evolution_service),
) -> list[AgentRanking]:
    """Get agents ranked by composite score with delta from previous period."""
    return await service.get_rankings(limit=limit, capability=capability)


@router.post("/{agent_id}/record", response_model=AgentScoreResponse, status_code=201)
async def record_score(
    agent_id: uuid.UUID,
    scores: ScoreInput,
    workflow_id: uuid.UUID | None = Query(default=None),
    service: EvolutionService = Depends(get_evolution_service),
) -> AgentScoreResponse:
    """Record a score snapshot for an agent (typically called by orchestrator)."""
    record = await service.record_score(agent_id, scores, workflow_id=workflow_id)
    return AgentScoreResponse.model_validate(record)


@router.post("/promote-demote", response_model=dict)
async def run_promotion_demotion(
    service: EvolutionService = Depends(get_evolution_service),
) -> dict:
    """Run promotion/demotion logic. Top 10% promoted, bottom 10% demoted."""
    return await service.promote_demote_agents()
