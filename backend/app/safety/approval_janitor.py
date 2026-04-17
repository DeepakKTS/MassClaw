"""Background janitor that reaps timed-out approval requests.

Approvals live in Redis with a TTL (default 300s). When the TTL fires
without a decision, the request is conceptually "expired" — but the
workflow that requested it is still parked in ``AWAITING_APPROVAL``
with no external signal to move it along. The scheduler's in-process
wait loop used to handle this, but the new exit-and-resume model
means no coroutine is left watching. This janitor fills that role:

1. Scans the Redis pending set every ``tick_seconds``.
2. For each entry past its ``expires_at``, marks it expired in Redis,
   publishes the ``approval.expired`` event, and writes a signed
   ``expired`` decision twin to the CRDT store (so peers reconcile).
3. Transitions the owning workflow to ``FAILED`` if it is still
   parked in ``AWAITING_APPROVAL``, so ``/status`` readers see a
   real terminal state.

The janitor is wired up in :func:`app.main.lifespan` as a single long-
running ``asyncio.Task``. Failures in a single tick are logged and
swallowed so the loop survives transient Redis / DB blips.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import update as sql_update

from app.core.database import db_session_context
from app.core.logging import get_logger
from app.core.redis import get_redis_manager
from app.models.base import WorkflowStatus
from app.models.workflow import Workflow
from app.safety.approval import ApprovalManager, ApprovalRequest

logger = get_logger(__name__)

DEFAULT_TICK_SECONDS = 30.0


async def _transition_workflow_to_failed(workflow_id: str, request_id: str) -> None:
    """If workflow is still AWAITING_APPROVAL, mark it FAILED with a timeout reason."""
    try:
        async with db_session_context() as session:
            await session.execute(
                sql_update(Workflow)
                .where(
                    Workflow.workflow_id == workflow_id,
                    Workflow.status == WorkflowStatus.AWAITING_APPROVAL,
                )
                .values(
                    status=WorkflowStatus.FAILED,
                    result={"error": "approval timeout", "request_id": request_id},
                    completed_at=datetime.now(UTC),
                )
            )
    except Exception as exc:
        logger.warning(
            "approval_janitor_workflow_transition_failed",
            workflow_id=workflow_id,
            request_id=request_id,
            error=str(exc),
        )


async def _reap_expired(mgr: ApprovalManager) -> tuple[int, int]:
    """Single tick: reap expired pending approvals. Returns (expired, transitioned)."""
    expired_count = 0
    transitioned_count = 0
    try:
        pending_ids: set[str] = await mgr._redis.smembers("approval:pending")
    except Exception as exc:
        logger.warning("approval_janitor_smembers_failed", error=str(exc))
        return 0, 0

    now = datetime.now(UTC)
    for rid in pending_ids:
        raw: str | None
        try:
            raw = await mgr._redis.get(mgr._key(rid))
        except Exception as exc:
            logger.warning("approval_janitor_get_failed", request_id=rid, error=str(exc))
            continue
        if raw is None:
            # TTL evicted the key but the pending-set entry is stale.
            try:
                await mgr._redis.srem("approval:pending", rid)
            except Exception:
                pass
            continue
        try:
            request = ApprovalRequest.model_validate_json(raw)
        except Exception as exc:
            logger.warning("approval_janitor_decode_failed", request_id=rid, error=str(exc))
            continue
        if request.status != "pending":
            continue
        try:
            expires_at = datetime.fromisoformat(request.expires_at)
        except ValueError:
            # Malformed ``expires_at`` — treat as already expired.
            expires_at = now
        if expires_at > now:
            continue

        # Transition to expired via ApprovalManager so event publishing +
        # CRDT decision twin fire through the same path as a human deny.
        try:
            await mgr._mark_expired(request)
            expired_count += 1
        except Exception as exc:
            logger.warning(
                "approval_janitor_mark_expired_failed",
                request_id=request.request_id,
                workflow_id=request.workflow_id,
                error=str(exc),
            )
            continue

        # Transition the owning workflow from AWAITING_APPROVAL → FAILED.
        await _transition_workflow_to_failed(request.workflow_id, request.request_id)
        transitioned_count += 1

    return expired_count, transitioned_count


async def approval_janitor_loop(*, tick_seconds: float = DEFAULT_TICK_SECONDS) -> None:
    """Long-running reaper loop; survives transient failures."""
    logger.info("approval_janitor_started", tick_seconds=tick_seconds)
    while True:
        try:
            mgr = ApprovalManager(get_redis_manager().get_cache_client())
            expired, transitioned = await _reap_expired(mgr)
            if expired or transitioned:
                logger.info(
                    "approval_janitor_tick",
                    expired=expired,
                    transitioned_workflows=transitioned,
                )
        except asyncio.CancelledError:
            logger.info("approval_janitor_stopped")
            raise
        except Exception as exc:
            logger.warning("approval_janitor_tick_failed", error=str(exc))
        await asyncio.sleep(tick_seconds)
