from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import PolicyAction, PolicyRuleType


class PolicyRuleCreate(BaseModel):
    """Request to create a policy rule."""

    name: str = Field(..., min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    rule_type: PolicyRuleType
    condition: dict = Field(
        ...,
        description='Condition JSON: e.g. {"field": "agent.trust_score", "op": "lt", "value": 0.3}',
    )
    action: PolicyAction
    priority: int = Field(..., ge=1, le=1000, description="Lower number = higher priority")
    enabled: bool = True


class PolicyRuleUpdate(BaseModel):
    """Request to update a policy rule."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = None
    rule_type: PolicyRuleType | None = None
    condition: dict | None = None
    action: PolicyAction | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    enabled: bool | None = None


class PolicyRuleResponse(BaseModel):
    """Policy rule response."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: uuid.UUID
    name: str
    description: str | None
    rule_type: PolicyRuleType
    condition: dict
    action: PolicyAction
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class PolicyEvaluationRequest(BaseModel):
    """Ad-hoc policy evaluation request."""

    action: str = Field(..., description="Action being evaluated, e.g. 'memory_write'")
    context: dict[str, Any] = Field(
        ..., description="Context for evaluation, e.g. {'agent.trust_score': 0.2}"
    )


class PolicyDecisionResponse(BaseModel):
    """Result of a policy evaluation."""

    approved: bool
    action: PolicyAction
    matched_rule: str | None = None
    reason: str
    violations: list[PolicyViolationDetail] = Field(default_factory=list)


class PolicyViolationDetail(BaseModel):
    """Detail of a single policy violation."""

    rule_name: str
    rule_id: uuid.UUID
    action: PolicyAction
    reason: str
