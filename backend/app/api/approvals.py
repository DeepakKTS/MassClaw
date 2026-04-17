from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.database import db_session_context, get_db_session
from app.core.events import EventBus
from app.core.logging import get_logger
from app.core.redis import get_redis_manager
from app.safety.approval import ApprovalManager, ApprovalRequest
from app.safety.federated_approval import FederatedApprovalStore
from app.services.identity_service import get_instance_key_store

router = APIRouter()
logger = get_logger(__name__)

# UUIDs are 36 chars; we cap a little higher to accept any identifier
# shape the approval system might emit but still reject absurdly long
# keys that could be DoS vectors against the Redis lookup.
_REQUEST_ID_MAX = 128


class ApprovalDecision(BaseModel):
    reason: str = Field(default="", max_length=1000)
    decided_by: str = Field(default="human", max_length=255)


class ApprovalEdit(BaseModel):
    reason: str = Field(default="", max_length=1000)
    decided_by: str = Field(default="human", max_length=255)
    edited_output: str = Field(..., min_length=1, max_length=50000)


def _serialise(obj: Any) -> dict[str, Any]:
    """Small helper — every ApprovalRequest is a Pydantic model so
    ``model_dump`` is the correct serialiser. The fallback to __dict__
    is defensive for future type changes."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj.__dict__)


async def _federated_store(session: AsyncSession) -> FederatedApprovalStore:
    keypair = get_instance_key_store().instance_keypair()
    return FederatedApprovalStore(session=session, keypair=keypair)


async def _hydrate_from_crdt(
    request_id: str,
    session: AsyncSession | None = None,
) -> ApprovalRequest | None:
    """Fetch the latest state of an approval from CRDT (cross-node fallback).

    If ``session`` is given, reuse it — important for endpoints whose
    writer path already holds a session. Otherwise open a short-lived
    one (used by read-only endpoints).
    """
    try:
        if session is not None:
            store = await _federated_store(session)
            return await store.find_by_request_id(request_id)
        async with db_session_context() as owned:
            store = await _federated_store(owned)
            return await store.find_by_request_id(request_id)
    except Exception as exc:
        logger.warning("federated_approval_hydrate_failed", request_id=request_id, error=str(exc))
        return None


@router.get("/pending")
async def list_pending_approvals(
    federated: bool = Query(
        True,
        description="Return the union of Redis pending + CRDT pending (for federated HITL).",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List all pending approval requests, optionally including federated peers."""
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    redis_pending: list[ApprovalRequest] = await mgr.get_pending()

    if not federated:
        return [_serialise(p) for p in redis_pending]

    seen = {p.request_id for p in redis_pending}
    merged = list(redis_pending)

    try:
        store = await _federated_store(session)
        crdt_pending = await store.get_pending()
    except Exception as exc:
        logger.warning("federated_approval_get_pending_failed", error=str(exc))
        crdt_pending = []

    for record in crdt_pending:
        if record.request_id in seen:
            continue
        seen.add(record.request_id)
        merged.append(record)

    return [_serialise(p) for p in merged]


@router.get("/stream")
async def stream_approvals() -> EventSourceResponse:
    """SSE stream of approval lifecycle events.

    Subscribes to the ``approval:*`` Redis pub/sub channel so the
    approvals page can update live instead of polling. Events are
    already emitted by :class:`ApprovalManager` on request / approve /
    deny / expire.
    """

    async def event_generator() -> AsyncGenerator[dict, None]:
        yield {
            "event": "connected",
            "data": json.dumps({"message": "approvals SSE stream connected"}),
        }

        async for event in EventBus.subscribe("approval", "*"):
            yield {
                "event": event.event_type,
                "data": json.dumps(event.data, default=str),
                "id": event.event_id,
            }

    return EventSourceResponse(event_generator())


@router.get("/{request_id}")
async def get_approval(
    request_id: str = Path(..., max_length=_REQUEST_ID_MAX, min_length=1),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get details of an approval request.

    Falls back to the CRDT twin if Redis has forgotten the request
    (TTL expired) or if the request lives on a federated peer.
    """
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        result = await mgr.check_status(request_id)
    except ValueError:
        result = await _hydrate_from_crdt(request_id, session=session)
        if result is None:
            raise HTTPException(
                status_code=404, detail=f"Approval request {request_id!r} not found in Redis or CRDT."
            ) from None
    return _serialise(result)


@router.post("/{request_id}/approve")
async def approve_request(
    data: ApprovalDecision,
    request_id: str = Path(..., max_length=_REQUEST_ID_MAX, min_length=1),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Approve a pending request.

    If Redis has evicted the entry (TTL), first re-hydrate it from the
    CRDT twin so the originator or any peer node can complete the
    decision without the request being lost.
    """
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        await mgr.approve(request_id, decided_by=data.decided_by)
    except ValueError as e:
        msg = str(e)
        if "already" in msg:
            raise HTTPException(status_code=409, detail=msg) from e
        # Missing in Redis — try CRDT fallback using the request's session
        # so writes inside the same transaction are visible.
        approval = await _hydrate_from_crdt(request_id, session=session)
        if approval is None:
            raise HTTPException(status_code=404, detail=msg) from e
        if approval.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Approval request {request_id!r} is '{approval.status}', cannot approve.",
            ) from e
        # Record the decision directly in CRDT — Redis is gone but the
        # signed twin is enough for the scheduler's resume fast-path.
        try:
            store = await _federated_store(session)
            approval.status = "approved"
            from datetime import UTC, datetime

            approval.decided_at = datetime.now(UTC).isoformat()
            approval.decided_by = data.decided_by
            await store.write_decision(approval)
            await EventBus.publish_dict(
                channel_parts=["approval", approval.workflow_id, "approved"],
                event_type="approval.approved",
                data={
                    "request_id": request_id,
                    "workflow_id": approval.workflow_id,
                    "task_id": approval.task_id,
                    "action": approval.action,
                    "decided_by": data.decided_by,
                    "checkpoint_hash": approval.checkpoint_hash,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("approval_approve_crdt_fallback_failed", request_id=request_id)
            raise HTTPException(status_code=500, detail="Approval write failed") from None
    except Exception:
        logger.exception("approval_approve_failed", request_id=request_id)
        raise HTTPException(status_code=500, detail="Approval write failed") from None
    logger.info("approval_approved", request_id=request_id, decided_by=data.decided_by)
    return {"status": "approved", "request_id": request_id}


@router.post("/{request_id}/deny")
async def deny_request(
    data: ApprovalDecision,
    request_id: str = Path(..., max_length=_REQUEST_ID_MAX, min_length=1),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Deny a pending request, with CRDT fallback mirroring approve."""
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        await mgr.deny(request_id, decided_by=data.decided_by, reason=data.reason or None)
    except ValueError as e:
        msg = str(e)
        if "already" in msg:
            raise HTTPException(status_code=409, detail=msg) from e
        approval = await _hydrate_from_crdt(request_id, session=session)
        if approval is None:
            raise HTTPException(status_code=404, detail=msg) from e
        if approval.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Approval request {request_id!r} is '{approval.status}', cannot deny.",
            ) from e
        try:
            store = await _federated_store(session)
            approval.status = "denied"
            from datetime import UTC, datetime

            approval.decided_at = datetime.now(UTC).isoformat()
            approval.decided_by = data.decided_by
            if data.reason:
                approval.context["denial_reason"] = data.reason
            await store.write_decision(approval)
            await EventBus.publish_dict(
                channel_parts=["approval", approval.workflow_id, "denied"],
                event_type="approval.denied",
                data={
                    "request_id": request_id,
                    "workflow_id": approval.workflow_id,
                    "task_id": approval.task_id,
                    "action": approval.action,
                    "decided_by": data.decided_by,
                    "reason": data.reason,
                    "checkpoint_hash": approval.checkpoint_hash,
                },
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("approval_deny_crdt_fallback_failed", request_id=request_id)
            raise HTTPException(status_code=500, detail="Approval write failed") from None
    except Exception:
        logger.exception("approval_deny_failed", request_id=request_id)
        raise HTTPException(status_code=500, detail="Approval write failed") from None
    logger.info("approval_denied", request_id=request_id, decided_by=data.decided_by)
    return {"status": "denied", "request_id": request_id}
