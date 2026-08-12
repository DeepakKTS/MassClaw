"""Background janitor that reaps timed-out approval requests.

Approvals live in Redis with a TTL (default 300s). When the TTL fires
without a decision, the request is conceptually "expired" — but the
workflow that requested it is still parked in ``AWAITING_APPROVAL``
with no external signal to move it along. The scheduler's in-process
wait loop used to handle this, but the new exit-and-resume model
means no coroutine is left watching. This janitor fills that role:

1. Scans the Redis pending set every ``tick_seconds``.
2. Scans the CRDT store for pending approvals too — an approval can be
   pending in either place. Ones gossiped in from a peer, and local ones
   whose Redis payload has aged out, exist only in the CRDT store.
3. For each entry past its ``expires_at``, marks it expired in Redis,
   publishes the ``approval.expired`` event, and writes a signed
   ``expired`` decision twin to the CRDT store (so peers reconcile).
4. Transitions the owning workflow to ``FAILED`` if it is still
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
from app.safety.approval import _PENDING_SET, ApprovalManager, ApprovalRequest

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


async def _crdt_pending(*, include_expired: bool = True) -> list[ApprovalRequest]:
    """Pending approvals known only to the CRDT store.

    Best-effort: needs a DB session and a keypair, either of which can be
    unavailable. A failure here must not stop the Redis pass.
    """
    try:
        from app.safety.federated_approval import FederatedApprovalStore
        from app.services.identity_service import get_instance_key_store

        keypair = get_instance_key_store().instance_keypair()
        async with db_session_context() as session:
            store = FederatedApprovalStore(session=session, keypair=keypair)
            return await store.get_pending(include_expired=include_expired)
    except Exception as exc:
        logger.warning("approval_janitor_crdt_scan_failed", error=str(exc))
        return []


async def _expire_one(mgr: ApprovalManager, request: ApprovalRequest) -> bool:
    """Mark a single request expired and fail its workflow.

    Goes through ``ApprovalManager._mark_expired`` so the ``approval.expired``
    event and the signed CRDT decision twin fire on exactly the same path a
    human deny would take. For a request that only existed in the CRDT store,
    that call also writes a short-lived Redis tombstone, which is harmless and
    keeps this to one code path.
    """
    try:
        await mgr._mark_expired(request)
    except Exception as exc:
        logger.warning(
            "approval_janitor_mark_expired_failed",
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            error=str(exc),
        )
        return False
    await _transition_workflow_to_failed(request.workflow_id, request.request_id)
    return True


async def _reap_expired(mgr: ApprovalManager) -> tuple[int, int]:
    """Single tick: reap expired pending approvals. Returns (expired, transitioned).

    Two passes, because an approval can be pending in either store:

    1. **Redis** — the fast primary path, enumerated from the pending set.
    2. **CRDT** — approvals gossiped in from a peer, or local ones whose Redis
       key aged out entirely. The janitor used to skip this pass, so those were
       never reaped and their workflows never left AWAITING_APPROVAL.
    """
    expired_count = 0
    transitioned_count = 0
    handled: set[str] = set()

    try:
        pending_ids: set[str] = await mgr._redis.smembers(_PENDING_SET)
    except Exception as exc:
        logger.warning("approval_janitor_smembers_failed", error=str(exc))
        pending_ids = set()

    # ---- Pass 1: Redis pending set -------------------------------------
    orphans: set[str] = set()
    for rid in pending_ids:
        raw: str | None
        try:
            raw = await mgr._redis.get(mgr._key(rid))
        except Exception as exc:
            logger.warning("approval_janitor_get_failed", request_id=rid, error=str(exc))
            continue
        if raw is None:
            # Payload gone but the set still lists it. Defer to the CRDT pass,
            # which may still know the workflow this belonged to — dropping the
            # id here is what used to strand the workflow.
            orphans.add(rid)
            continue
        try:
            request = ApprovalRequest.model_validate_json(raw)
        except Exception as exc:
            logger.warning("approval_janitor_decode_failed", request_id=rid, error=str(exc))
            continue
        if request.status != "pending" or not request.is_expired():
            continue

        handled.add(request.request_id)
        if await _expire_one(mgr, request):
            expired_count += 1
            transitioned_count += 1

    # ---- Pass 2: CRDT-sourced pending ----------------------------------
    # Covers both the orphans above and approvals this node only ever learned
    # about by gossip.
    for request in await _crdt_pending():
        if request.request_id in handled or not request.is_expired():
            continue
        handled.add(request.request_id)
        if await _expire_one(mgr, request):
            expired_count += 1
            transitioned_count += 1
        orphans.discard(request.request_id)

    # ---- Clean up ids nothing could account for -------------------------
    # No payload and no CRDT twin, so there is no workflow_id to recover and
    # nothing to reap. Logged rather than dropped silently: a run of these
    # means something upstream is leaking pending-set entries.
    unaccounted = orphans - handled
    if unaccounted:
        try:
            await mgr._redis.srem(_PENDING_SET, *unaccounted)
        except Exception as exc:
            logger.warning("approval_janitor_srem_failed", error=str(exc))
        logger.info(
            "approval_janitor_dropped_unresolvable_pending",
            count=len(unaccounted),
            request_ids=sorted(unaccounted)[:10],
        )

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
