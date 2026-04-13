from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TrustScoreInput(BaseModel):
    """Input scores for trust calculation."""

    quality_score: float = Field(..., ge=0, le=1)
    latency_score: float = Field(..., ge=0, le=1)
    cost_score: float = Field(..., ge=0, le=1)
    consistency_score: float = Field(..., ge=0, le=1)
    reliability_score: float = Field(..., ge=0, le=1)


class TrustEventResponse(BaseModel):
    """Trust event history entry."""

    model_config = ConfigDict(from_attributes=True)

    trust_event_id: uuid.UUID
    agent_id: uuid.UUID
    workflow_id: uuid.UUID | None
    task_id: uuid.UUID | None
    quality_score: float
    latency_score: float
    cost_score: float
    consistency_score: float
    reliability_score: float
    composite_score: float
    old_trust: float
    new_trust: float
    created_at: datetime


class TrustSummary(BaseModel):
    """Agent trust summary for leaderboard."""

    agent_id: uuid.UUID
    agent_name: str
    trust_score: float
    total_interactions: int
    trend: float = Field(description="Score change over last period")
    rank: int


class TrustBreakdown(BaseModel):
    """Detailed trust breakdown for a single agent."""

    agent_id: uuid.UUID
    current_trust: float
    quality_avg: float
    speed_avg: float
    cost_avg: float
    consistency_avg: float
    reliability_avg: float
    total_interactions: int
    last_updated: datetime | None
