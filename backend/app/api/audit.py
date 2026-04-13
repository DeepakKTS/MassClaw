from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_audit_service
from app.models.base import ActorType, AuditEventType
from app.schemas.audit import AuditLogResponse, AuditQueryParams
from app.schemas.common import PaginatedResponse, PaginationParams
from app.services.audit_service import AuditService

router = APIRouter()


@router.get(
    "/workflow/{workflow_id}",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="Workflow audit trail",
)
async def get_workflow_audit(
    workflow_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: AuditService = Depends(get_audit_service),
) -> PaginatedResponse[AuditLogResponse]:
    """Return a paginated audit trail for a specific workflow."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.get_workflow_audit(workflow_id, pagination)


@router.get(
    "/agent/{agent_id}",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="Agent audit trail",
)
async def get_agent_audit(
    agent_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: AuditService = Depends(get_audit_service),
) -> PaginatedResponse[AuditLogResponse]:
    """Return a paginated audit trail for a specific agent."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.get_agent_audit(agent_id, pagination)


@router.get(
    "/search",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="Search audit logs",
)
async def search_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    event_type: AuditEventType | None = Query(default=None),
    actor_type: ActorType | None = Query(default=None),
    actor_id: str | None = Query(default=None, max_length=255),
    workflow_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=500, description="Full-text search on summaries"),
    since: datetime | None = Query(default=None, description="ISO 8601 lower bound"),
    until: datetime | None = Query(default=None, description="ISO 8601 upper bound"),
    service: AuditService = Depends(get_audit_service),
) -> PaginatedResponse[AuditLogResponse]:
    """Full query with filtering by event_type, actor_type, actor_id,
    workflow_id, time range, and optional full-text search on summaries."""
    pagination = PaginationParams(page=page, page_size=page_size)
    params = AuditQueryParams(
        workflow_id=workflow_id,
        agent_id=actor_id,
        event_type=event_type,
        actor_type=actor_type,
        search=search,
        since=since,
        until=until,
    )
    return await service.query(params, pagination)


@router.get(
    "/stats",
    response_model=dict[str, int],
    summary="Audit event statistics",
)
async def get_audit_stats(
    workflow_id: uuid.UUID | None = Query(default=None),
    since: datetime | None = Query(default=None, description="ISO 8601 lower bound"),
    service: AuditService = Depends(get_audit_service),
) -> dict[str, int]:
    """Return event counts grouped by event type.

    Optionally filter by workflow_id and/or a lower time bound.
    """
    return await service.count_events(workflow_id=workflow_id, since=since)
