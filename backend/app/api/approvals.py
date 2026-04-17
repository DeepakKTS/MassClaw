from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.redis import get_redis_manager
from app.safety.approval import ApprovalManager

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


@router.get("/pending")
async def list_pending_approvals() -> list[dict[str, Any]]:
    """List all pending approval requests."""
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    pending = await mgr.get_pending()
    return [_serialise(p) for p in pending]


@router.get("/{request_id}")
async def get_approval(
    request_id: str = Path(..., max_length=_REQUEST_ID_MAX, min_length=1),
) -> dict[str, Any]:
    """Get details of an approval request."""
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        result = await mgr.check_status(request_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _serialise(result)


@router.post("/{request_id}/approve")
async def approve_request(
    data: ApprovalDecision,
    request_id: str = Path(..., max_length=_REQUEST_ID_MAX, min_length=1),
) -> dict[str, str]:
    """Approve a pending request."""
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        await mgr.approve(request_id, decided_by=data.decided_by)
    except ValueError as e:
        # ValueError from ApprovalManager covers both missing and
        # already-decided requests. Map to 404 / 409 based on message.
        msg = str(e)
        status = 409 if "already" in msg else 404
        raise HTTPException(status_code=status, detail=msg) from e
    except Exception:
        logger.exception("approval_approve_failed", request_id=request_id)
        raise HTTPException(status_code=500, detail="Approval write failed") from None
    logger.info("approval_approved", request_id=request_id, decided_by=data.decided_by)
    return {"status": "approved", "request_id": request_id}


@router.post("/{request_id}/deny")
async def deny_request(
    data: ApprovalDecision,
    request_id: str = Path(..., max_length=_REQUEST_ID_MAX, min_length=1),
) -> dict[str, str]:
    """Deny a pending request."""
    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        await mgr.deny(request_id, decided_by=data.decided_by, reason=data.reason or None)
    except ValueError as e:
        msg = str(e)
        status = 409 if "already" in msg else 404
        raise HTTPException(status_code=status, detail=msg) from e
    except Exception:
        logger.exception("approval_deny_failed", request_id=request_id)
        raise HTTPException(status_code=500, detail="Approval write failed") from None
    logger.info("approval_denied", request_id=request_id, decided_by=data.decided_by)
    return {"status": "denied", "request_id": request_id}
