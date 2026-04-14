from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import ActorType, AuditEventType


class AuditLogResponse(BaseModel):
    """Audit log entry."""

    model_config = ConfigDict(from_attributes=True)

    log_id: uuid.UUID
    workflow_id: uuid.UUID | None
    task_id: uuid.UUID | None
    event_type: AuditEventType
    actor_type: ActorType
    actor_id: str
    input_summary: str | None
    output_summary: str | None
    decision_reason: str | None
    metadata: dict = Field(alias="metadata_")
    created_at: datetime


class AuditQueryParams(BaseModel):
    """Parameters for querying audit logs."""

    workflow_id: uuid.UUID | None = None
    agent_id: str | None = None
    event_type: AuditEventType | None = None
    actor_type: ActorType | None = None
    search: str | None = Field(default=None, max_length=500, description="Full-text search on summaries")
    since: datetime | None = None
    until: datetime | None = None
