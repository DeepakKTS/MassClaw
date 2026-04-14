from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class ApprovalDecision(BaseModel):
    reason: str = Field(default="", max_length=1000)
    decided_by: str = Field(default="human", max_length=255)


class ApprovalEdit(BaseModel):
    reason: str = Field(default="", max_length=1000)
    decided_by: str = Field(default="human", max_length=255)
    edited_output: str = Field(..., min_length=1, max_length=50000)


@router.get("/pending")
async def list_pending_approvals() -> list[dict[str, Any]]:
    """List all pending approval requests."""
    from app.core.redis import get_redis_manager
    from app.safety.approval import ApprovalManager

    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    pending = await mgr.get_pending()
    return [p.model_dump() if hasattr(p, "model_dump") else p.__dict__ for p in pending]


@router.get("/{request_id}")
async def get_approval(request_id: str) -> dict[str, Any]:
    """Get details of an approval request."""
    from app.core.redis import get_redis_manager
    from app.safety.approval import ApprovalManager

    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        result = await mgr.check_status(request_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return result.model_dump() if hasattr(result, "model_dump") else result.__dict__


@router.post("/{request_id}/approve")
async def approve_request(request_id: str, data: ApprovalDecision) -> dict[str, str]:
    """Approve a pending request."""
    from app.core.redis import get_redis_manager
    from app.safety.approval import ApprovalManager

    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        await mgr.approve(request_id, decided_by=data.decided_by)
        return {"status": "approved", "request_id": request_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{request_id}/deny")
async def deny_request(request_id: str, data: ApprovalDecision) -> dict[str, str]:
    """Deny a pending request."""
    from app.core.redis import get_redis_manager
    from app.safety.approval import ApprovalManager

    mgr = ApprovalManager(get_redis_manager().get_cache_client())
    try:
        await mgr.deny(request_id, decided_by=data.decided_by, reason=data.reason or None)
        return {"status": "denied", "request_id": request_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
