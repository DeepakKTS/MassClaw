from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import WorkflowStatus


class WorkflowCreate(BaseModel):
    """Request schema for creating a new workflow."""

    prompt: str = Field(
        ..., min_length=10, max_length=50000, description="User prompt / task description"
    )
    user_id: str = Field(default="default", max_length=255)
    domain: str | None = Field(
        default=None, max_length=100, description="Domain hint (auto-detected if not provided)"
    )
    budget_limit: float = Field(
        ..., gt=0, description="Maximum budget in credits"
    )
    priority: int = Field(default=5, ge=1, le=10, description="Workflow priority (1=highest)")
    metadata: dict = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    """Full workflow response."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: uuid.UUID
    user_id: str
    prompt: str
    domain: str | None
    status: WorkflowStatus
    budget_limit: float
    budget_used: float
    priority: int
    metadata: dict = Field(alias="metadata_")
    dag_snapshot: dict | None
    result: dict | None
    execution_mode: str | None = None
    goal_snapshot: dict | None = None
    reasoning_summary: dict | None = None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkflowStatusResponse(BaseModel):
    """Workflow status with progress info."""

    workflow_id: uuid.UUID
    status: WorkflowStatus
    progress_percent: float = Field(ge=0, le=100)
    total_tasks: int
    completed_tasks: int
    running_tasks: int
    failed_tasks: int
    budget_used: float
    budget_limit: float
    started_at: datetime | None
    elapsed_seconds: float | None = None


class WorkflowResultResponse(BaseModel):
    """Final workflow result."""

    workflow_id: uuid.UUID
    status: WorkflowStatus
    result: dict | None
    confidence: float | None
    total_cost: float
    total_latency_ms: float
    tasks_completed: int
    tasks_failed: int
    completed_at: datetime | None
