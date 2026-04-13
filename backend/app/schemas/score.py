from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScoreInput(BaseModel):
    """Input for recording an agent score."""

    quality: float = Field(..., ge=0, le=1)
    speed: float = Field(..., ge=0, le=1)
    cost_efficiency: float = Field(..., ge=0, le=1)
    consistency: float = Field(..., ge=0, le=1)
    reliability: float = Field(..., ge=0, le=1)


class AgentScoreResponse(BaseModel):
    """Agent score record."""

    model_config = ConfigDict(from_attributes=True)

    score_id: uuid.UUID
    agent_id: uuid.UUID
    workflow_id: uuid.UUID | None
    quality: float
    speed: float
    cost_efficiency: float
    consistency: float
    reliability: float
    composite: float
    created_at: datetime


class AgentEvolution(BaseModel):
    """Agent evolution summary with trends."""

    agent_id: uuid.UUID
    agent_name: str
    current_composite: float
    percentile: float = Field(ge=0, le=100)
    trend_7d: float = Field(description="Score delta over last 7 days")
    dimensions: DimensionScores
    total_scored_tasks: int


class DimensionScores(BaseModel):
    """Breakdown of scores by dimension."""

    quality: float
    speed: float
    cost_efficiency: float
    consistency: float
    reliability: float


class AgentRanking(BaseModel):
    """Agent ranking entry."""

    rank: int
    agent_id: uuid.UUID
    agent_name: str
    composite_score: float
    delta_from_previous: float
    total_tasks: int
