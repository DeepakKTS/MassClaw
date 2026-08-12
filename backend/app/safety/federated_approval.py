"""Federated approval replication via the CRDT memory store.

Approval requests and decisions live in Redis (fast path, owned by
:class:`app.safety.approval.ApprovalManager`). For cross-node HITL we
also persist a signed memory-record twin so any peer can discover
pending approvals by gossip and any peer can resume after a decision
has been recorded on the originating node.

Two record shapes are used, both as :data:`MemoryType.META` records:

- ``approval_status=pending`` — written when the approval request is
  created. Its ``metadata_[APPROVAL_REQUEST_ID_KEY]`` ties it back to
  the Redis key.
- ``approval_status=approved|denied|expired`` — written when the
  decision is made. Reading the union of both record kinds and
  dropping requests that already have a decision yields the federated
  ``get_pending`` view.

This module is intentionally narrow: it signs + stores + reads the two
record kinds, and nothing else. The Redis-vs-CRDT reconciliation
(preferring the faster Redis answer when both exist) lives one layer
up, in :mod:`app.api.approvals`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.crdt.store import CRDTStore
from app.identity.did import build_did_key
from app.identity.signer import KeyPair
from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord
from app.safety.approval import ApprovalRequest

logger = get_logger(__name__)

APPROVAL_MARKER_KEY = "approval_marker"
APPROVAL_REQUEST_ID_KEY = "approval_request_id"
APPROVAL_STATUS_KEY = "approval_status"
APPROVAL_PAYLOAD_KEY = "approval_payload"
APPROVAL_NODE_ID_KEY = "approval_node_id"


def _metadata_for(request: ApprovalRequest, *, node_id: str | None) -> dict[str, Any]:
    """Shape the memory-record metadata that marks a row as an approval twin."""
    meta: dict[str, Any] = {
        APPROVAL_MARKER_KEY: True,
        APPROVAL_REQUEST_ID_KEY: request.request_id,
        APPROVAL_STATUS_KEY: request.status,
        APPROVAL_PAYLOAD_KEY: request.model_dump(mode="json"),
    }
    if node_id is not None:
        meta[APPROVAL_NODE_ID_KEY] = node_id
    return meta


def is_approval_record(record: MemoryRecord) -> bool:
    """Return True if ``record`` is a persisted approval twin."""
    meta = record.metadata_ or {}
    return bool(meta.get(APPROVAL_MARKER_KEY)) and record.memory_type == MemoryType.META


def _decode_record(record: MemoryRecord) -> ApprovalRequest | None:
    """Rebuild an :class:`ApprovalRequest` from an approval memory-record."""
    meta = record.metadata_ or {}
    payload = meta.get(APPROVAL_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        return ApprovalRequest.model_validate(payload)
    except Exception as exc:
        logger.warning(
            "federated_approval_decode_failed",
            content_hash=(record.content_hash or "")[:12],
            error=str(exc),
        )
        return None


def _record_node_id(record: MemoryRecord) -> str | None:
    meta = record.metadata_ or {}
    value = meta.get(APPROVAL_NODE_ID_KEY)
    return str(value) if value is not None else None


class FederatedApprovalStore:
    """Persist + retrieve approval twins via the CRDT memory store.

    Parameters
    ----------
    session:
        A live :class:`AsyncSession` — the caller owns the transaction.
    keypair:
        The instance keypair used to sign records.
    """

    def __init__(self, session: AsyncSession, keypair: KeyPair) -> None:
        self._session = session
        self._keypair = keypair
        self._author_did = build_did_key(keypair.public_bytes)
        self._store = CRDTStore(session=session)

    @property
    def author_did(self) -> str:
        return self._author_did

    # ---------------------------------------------------------------- writes

    async def write_request(
        self,
        request: ApprovalRequest,
        *,
        node_id: str | None = None,
    ) -> str:
        """Sign + persist the ``pending`` state of an approval request.

        ``node_id`` is the DAG node that triggered the approval — used by
        scheduler-on-resume to look up a decision for the right node.
        """
        content = f"approval.request:{request.request_id}"
        try:
            workflow_uuid = uuid.UUID(request.workflow_id)
        except ValueError:
            # Legacy / synthetic workflow IDs in tests. Fall back to the
            # deterministic namespace UUID so the record still lands.
            workflow_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"approval:{request.workflow_id}")
        record = await self._store.put(
            workflow_id=workflow_uuid,
            memory_type=MemoryType.META,
            content=content,
            confidence=1.0,
            metadata=_metadata_for(request, node_id=node_id),
            parent_hashes=[],
            author_did=self._author_did,
            keypair=self._keypair,
            record_state=RecordState.ACTIVE,
        )
        logger.info(
            "federated_approval_request_written",
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            node_id=node_id,
            hash=(record.content_hash or "")[:12],
        )
        return record.content_hash or ""

    async def write_decision(
        self,
        request: ApprovalRequest,
        *,
        node_id: str | None = None,
    ) -> str:
        """Sign + persist the terminal state (approved/denied/expired)."""
        if request.status == "pending":
            raise ValueError(f"refusing to write_decision for pending request {request.request_id!r}")
        content = f"approval.decision:{request.request_id}:{request.status}"
        try:
            workflow_uuid = uuid.UUID(request.workflow_id)
        except ValueError:
            workflow_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"approval:{request.workflow_id}")
        record = await self._store.put(
            workflow_id=workflow_uuid,
            memory_type=MemoryType.META,
            content=content,
            confidence=1.0,
            metadata=_metadata_for(request, node_id=node_id),
            parent_hashes=[],
            author_did=self._author_did,
            keypair=self._keypair,
            record_state=RecordState.ACTIVE,
        )
        logger.info(
            "federated_approval_decision_written",
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            status=request.status,
            decided_by=request.decided_by,
            hash=(record.content_hash or "")[:12],
        )
        return record.content_hash or ""

    # ---------------------------------------------------------------- reads

    async def _scan(self) -> list[MemoryRecord]:
        result = await self._session.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.memory_type == MemoryType.META,
                MemoryRecord.record_state == RecordState.ACTIVE,
            )
            .order_by(MemoryRecord.created_at.desc())
        )
        return [r for r in result.scalars().all() if is_approval_record(r)]

    async def get_pending(
        self,
        workflow_id: str | None = None,
        *,
        include_expired: bool = False,
    ) -> list[ApprovalRequest]:
        """Return all pending approvals, de-duped by ``request_id``.

        An approval is considered decided if any record with the same
        ``request_id`` has a non-``pending`` status. The latest record
        (most recent ``created_at``) wins for the returned representation.

        Requests past ``expires_at`` are excluded by default. This view used to
        ignore expiry entirely, so a request whose deadline had long passed was
        still reported as actionable — indefinitely, since nothing else was
        looking at CRDT-sourced approvals either. ``ApprovalManager.get_pending``
        has always wall-clock filtered; this keeps the two consistent.

        Reaping is still the janitor's job (it writes the ``expired`` decision
        twin that peers reconcile against). This only stops an expired request
        being *displayed* as live in the window before a tick lands.

        Parameters
        ----------
        include_expired:
            Set by the janitor, which specifically needs the expired ones.
        """
        by_request: dict[str, tuple[ApprovalRequest, bool]] = {}
        for record in await self._scan():
            req = _decode_record(record)
            if req is None:
                continue
            if workflow_id is not None and req.workflow_id != workflow_id:
                continue
            prev = by_request.get(req.request_id)
            if prev is None:
                by_request[req.request_id] = (req, req.status != "pending")
                continue
            _, already_decided = prev
            if req.status != "pending":
                # Any decision record knocks the pending flag out.
                by_request[req.request_id] = (req, True)
            elif not already_decided:
                # Keep the freshest pending representation.
                by_request[req.request_id] = (req, False)
        return [
            req for req, decided in by_request.values() if not decided and (include_expired or not req.is_expired())
        ]

    async def find_by_request_id(self, request_id: str) -> ApprovalRequest | None:
        """Return the latest state (pending/decided) for a request, or None."""
        return (await self.find_many_by_request_id([request_id])).get(request_id)

    async def find_many_by_request_id(
        self,
        request_ids: Iterable[str],
    ) -> dict[str, ApprovalRequest]:
        """Latest state for several requests in a single scan.

        Same resolution rule as :meth:`find_by_request_id` — a decision beats a
        pending record, and ``_scan`` returns newest first so the first match in
        each class wins. Callers holding a list of ids used to loop over the
        single-id lookup, and since every call rescans *all* ``META`` records
        that made a page of approvals cost one full scan per row.

        Ids with no record are absent from the result rather than mapped to
        ``None``, so ``.get(id)`` reproduces the old return value exactly.
        """
        wanted = set(request_ids)
        if not wanted:
            return {}

        decisions: dict[str, ApprovalRequest] = {}
        pendings: dict[str, ApprovalRequest] = {}
        for record in await self._scan():
            req = _decode_record(record)
            if req is None or req.request_id not in wanted:
                continue
            if req.status == "pending":
                pendings.setdefault(req.request_id, req)
            else:
                decisions.setdefault(req.request_id, req)

        return {rid: decisions.get(rid) or pendings[rid] for rid in pendings.keys() | decisions.keys()}

    async def find_decision_for_node(
        self,
        workflow_id: uuid.UUID,
        node_id: str,
    ) -> ApprovalRequest | None:
        """Scheduler-on-resume helper: has this (workflow, node) been decided?

        Request records carry ``approval_node_id`` in metadata (the scheduler
        writes them with the triggering DAG node). Decision records are
        written by :class:`ApprovalManager` without a node_id — they only
        know the ``request_id``. We join request ↔ decision by request_id
        to return the final decision for the pair.
        """
        workflow_id_str = str(workflow_id)
        # First pass: find the request record that matches (workflow, node)
        # and pick up its request_id.
        matching_request_ids: set[str] = set()
        decisions_by_request: dict[str, ApprovalRequest] = {}
        for record in await self._scan():
            req = _decode_record(record)
            if req is None:
                continue
            if req.workflow_id != workflow_id_str:
                continue
            if req.status == "pending":
                if _record_node_id(record) == node_id:
                    matching_request_ids.add(req.request_id)
            else:
                # Decision records may not have node_id — index by request_id.
                decisions_by_request.setdefault(req.request_id, req)
                # Some decision records also carry node_id (legacy /
                # scheduler-driven writes); accept a direct match.
                if _record_node_id(record) == node_id:
                    return req
        # Second pass: return the decision for any request that matched.
        for rid in matching_request_ids:
            decided = decisions_by_request.get(rid)
            if decided is not None:
                return decided
        return None
