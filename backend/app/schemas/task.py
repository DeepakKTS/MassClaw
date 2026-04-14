from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.base import TaskStatus


class TaskResponse(BaseModel):
    """Full task response."""

    model_config = ConfigDict(from_attributes=True)

    task_id: uuid.UUID
    workflow_id: uuid.UUID
    parent_task_id: uuid.UUID | None
    assigned_agent_id: uuid.UUID | None
    step_number: int
    capability: str
    description: str
    status: TaskStatus
    input: dict | None
    output: dict | None
    confidence: float | None
    retry_count: int
    max_retries: int
    cost_used: float
    latency_ms: float | None
    error_message: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskSummary(BaseModel):
    """Lightweight task for list views."""

    model_config = ConfigDict(from_attributes=True)

    task_id: uuid.UUID
    step_number: int
    capability: str
    status: TaskStatus
    assigned_agent_id: uuid.UUID | None
    confidence: float | None
    cost_used: float
    latency_ms: float | None
