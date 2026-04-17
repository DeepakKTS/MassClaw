from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
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


# ---------------------------------------------------------------------------
# Policy decisions — backed by the signed CRDT memory records that
# :mod:`app.safety.audit` writes. Every decision is verifiable on its
# own (content-addressed hash + Ed25519 signature).
# ---------------------------------------------------------------------------


@router.get(
    "/policy-decisions",
    summary="List signed policy-decision audit records",
)
async def list_policy_decisions(
    workflow_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(
        default=None,
        description="Filter by decision action (allow, deny, escalate_human).",
        max_length=64,
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return signed policy-decision records newest-first."""
    from fastapi import HTTPException

    from app.safety.audit import (
        POLICY_CONTEXT_SUMMARY_KEY,
        POLICY_DECISION_PAYLOAD_KEY,
        list_decisions,
    )
    from app.safety.decision import DecisionAction

    parsed_action: DecisionAction | None = None
    if action is not None:
        try:
            parsed_action = DecisionAction(action)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"unknown action {action!r}") from exc

    records = await list_decisions(
        session,
        workflow_id=workflow_id,
        action=parsed_action,
        limit=limit,
        offset=offset,
    )

    return [
        {
            "content_hash": r.content_hash,
            "workflow_id": str(r.workflow_id),
            "author_did": r.author_did,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "content": r.content,
            "decision": (r.metadata_ or {}).get(POLICY_DECISION_PAYLOAD_KEY),
            "context": (r.metadata_ or {}).get(POLICY_CONTEXT_SUMMARY_KEY),
        }
        for r in records
    ]
