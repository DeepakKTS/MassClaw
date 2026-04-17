"""Signed audit trail for policy decisions.

Every non-abstain :class:`Decision` produced by the registry becomes a
signed CRDT memory record so the audit query path is just a memory
query — no separate audit DB, no divergence between "what was
decided" and "what we tell the reviewer". Matches the design doc:

> Every decision produces a signed record written to shared memory
> (same pipeline as Pillar B). Audit queries are just memory queries.

Metadata markers:
- ``"policy_decision": True`` — top-level marker for listing queries.
- ``"decision"`` — the full :meth:`Decision.to_audit_payload`.
- ``"context_summary"`` — a trimmed, side-effect-free snapshot of the
  :class:`PolicyContext` (no session/redis handles, extra shallow-copied).

Failures to persist are logged and swallowed. The scheduler must not
crash because the audit trail couldn't be written — losing auditability
for one decision is less bad than losing the run.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.crdt.store import CRDTStore
from app.identity.did import build_did_key
from app.identity.signer import KeyPair
from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord
from app.safety.context import PolicyContext
from app.safety.decision import Decision, DecisionAction

logger = get_logger(__name__)

POLICY_DECISION_MARKER_KEY = "policy_decision"
POLICY_DECISION_PAYLOAD_KEY = "decision"
POLICY_CONTEXT_SUMMARY_KEY = "context_summary"


def _safe_context_summary(ctx: PolicyContext) -> dict[str, Any]:
    """Trim ``ctx`` to a JSON-serialisable dict suitable for storage.

    Drops the ``session`` and ``redis`` handles (not serialisable, not
    meaningful in a historical record) and converts UUIDs to strings.
    """
    return {
        "agent_did": ctx.agent_did,
        "agent_trust_score": ctx.agent_trust_score,
        "agent_capabilities": list(ctx.agent_capabilities),
        "agent_id": str(ctx.agent_id) if ctx.agent_id else None,
        "action": ctx.action,
        "action_category": ctx.action_category,
        "tool_name": ctx.tool_name,
        "amount": ctx.amount,
        "target": ctx.target,
        "estimated_cost": ctx.estimated_cost,
        "workflow_id": str(ctx.workflow_id) if ctx.workflow_id else None,
        "task_id": str(ctx.task_id) if ctx.task_id else None,
        "now": ctx.now.isoformat(),
        "extra": dict(ctx.extra),
    }


def is_policy_decision_record(record: MemoryRecord) -> bool:
    """True when ``record`` is a persisted policy-decision audit entry."""
    meta = record.metadata_ or {}
    return bool(meta.get(POLICY_DECISION_MARKER_KEY)) and record.memory_type == MemoryType.META


async def record_decision(
    *,
    session: AsyncSession,
    keypair: KeyPair,
    decision: Decision,
    ctx: PolicyContext,
) -> MemoryRecord | None:
    """Persist ``decision`` as a signed META memory record.

    Abstain decisions are deliberately NOT persisted — a rule that
    didn't speak shouldn't generate audit noise. If ``ctx.workflow_id``
    is missing we can't write (FK on memory_records) so we log and
    return ``None``.
    """
    if decision.action is DecisionAction.ABSTAIN:
        return None
    if ctx.workflow_id is None:
        logger.warning(
            "policy_decision_audit_missing_workflow_id",
            rule_id=decision.rule_id,
            action=decision.action.value,
        )
        return None

    store = CRDTStore(session=session)
    author_did = build_did_key(keypair.public_bytes)

    metadata: dict[str, Any] = {
        POLICY_DECISION_MARKER_KEY: True,
        POLICY_DECISION_PAYLOAD_KEY: decision.to_audit_payload(),
        POLICY_CONTEXT_SUMMARY_KEY: _safe_context_summary(ctx),
    }
    content = f"policy.{decision.action.value}: {decision.reason or decision.rule_id or 'unspecified'}"

    try:
        record = await store.put(
            workflow_id=ctx.workflow_id,
            memory_type=MemoryType.META,
            content=content[:2000],
            confidence=1.0,
            metadata=metadata,
            parent_hashes=[],
            author_did=author_did,
            keypair=keypair,
            record_state=RecordState.ACTIVE,
        )
    except Exception as exc:
        logger.warning(
            "policy_decision_audit_write_failed",
            rule_id=decision.rule_id,
            action=decision.action.value,
            error=str(exc),
        )
        return None

    logger.info(
        "policy_decision_audited",
        rule_id=decision.rule_id,
        action=decision.action.value,
        workflow_id=str(ctx.workflow_id),
        hash=(record.content_hash or "")[:12],
    )
    return record


async def list_decisions(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID | None = None,
    action: DecisionAction | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MemoryRecord]:
    """List persisted policy-decision records, newest first.

    Filtering is done in Python after the SQL filter because the
    metadata marker lives inside a JSONB column — an @> operator would
    work too, but Python filtering is simpler and fast enough for the
    audit surface's access patterns.
    """
    stmt = select(MemoryRecord).where(MemoryRecord.memory_type == MemoryType.META)
    if workflow_id is not None:
        stmt = stmt.where(MemoryRecord.workflow_id == workflow_id)
    stmt = stmt.order_by(MemoryRecord.created_at.desc()).limit(limit + offset)
    rows = (await session.execute(stmt)).scalars().all()

    out: list[MemoryRecord] = []
    skipped = 0
    for row in rows:
        if not is_policy_decision_record(row):
            continue
        if action is not None:
            payload = (row.metadata_ or {}).get(POLICY_DECISION_PAYLOAD_KEY) or {}
            if payload.get("action") != action.value:
                continue
        if skipped < offset:
            skipped += 1
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out
