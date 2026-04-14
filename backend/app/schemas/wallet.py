from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import WalletActionType


class WalletChargeRequest(BaseModel):
    """Request to charge credits for a task."""

    workflow_id: uuid.UUID
    agent_id: uuid.UUID
    amount: float = Field(..., gt=0)
    reason: str = Field(..., max_length=500)
    metadata: dict = Field(default_factory=dict)


class WalletEventResponse(BaseModel):
    """Wallet event record."""

    model_config = ConfigDict(from_attributes=True)

    wallet_event_id: uuid.UUID
    workflow_id: uuid.UUID
    agent_id: uuid.UUID | None
    action_type: WalletActionType
    credit_delta: float
    balance_after: float
    reason: str | None
    metadata: dict = Field(alias="metadata_")
    created_at: datetime


class WalletBalanceResponse(BaseModel):
    """Workflow wallet balance."""

    workflow_id: uuid.UUID
    budget_limit: float
    budget_used: float
    budget_remaining: float
    reserved: float
    available: float  # remaining - reserved


class CostEstimateItem(BaseModel):
    """Cost estimate per agent/step."""

    capability: str
    agent_name: str | None = None
    estimated_tokens: int
    estimated_cost: float


class CostEstimate(BaseModel):
    """Pre-flight cost estimation."""

    total_estimated_cost: float
    breakdown: list[CostEstimateItem]
    confidence: float = Field(ge=0, le=1)
