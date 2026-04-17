"""Workflow checkpoints — serialised, signed, federation-ready.

A checkpoint captures enough state to resume a workflow later on **any**
MassClaw node in the federation. The state is packaged as a signed
``META`` memory record, which means:

- The ``CRDTStore`` enforces Ed25519 signature verification before
  persisting, so a malformed checkpoint never lands.
- The checkpoint is automatically addressable by its content hash and
  participates in the Merkle sync protocol — peers learn about it
  through the same gossip loop that handles ordinary memory.
- Any node with a valid :class:`MemoryRecord` for the checkpoint hash
  can reconstruct the workflow state and resume execution, which is the
  Day-13 cross-node-resume primitive.

This module is pure plumbing: it does not know about the scheduler's
internal state machine or the reflection engine. It takes a DAG + a few
pieces of metadata, hands them to the CRDT store, and hands a frozen
record back on load.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.crdt.store import CRDTStore
from app.identity.did import build_did_key
from app.identity.signer import KeyPair
from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord
from app.orchestration.dag import DAG

logger = get_logger(__name__)

CHECKPOINT_VERSION = 1
CHECKPOINT_MARKER_KEY = "checkpoint"
CHECKPOINT_REASON_KEY = "checkpoint_reason"
CHECKPOINT_WORKFLOW_KEY = "checkpoint_workflow_id"
CHECKPOINT_PAYLOAD_KEY = "checkpoint_payload"


class CheckpointError(Exception):
    """Base class for checkpoint failures."""


class CheckpointDecodeError(CheckpointError):
    """Raised when a persisted checkpoint cannot be decoded back to a WorkflowCheckpoint."""


@dataclass(frozen=True)
class WorkflowCheckpoint:
    """Immutable snapshot of a workflow's resumable state.

    Not every field is required — the minimum is ``workflow_id`` +
    ``dag_snapshot``. Reflection and variable state are opt-in for
    callers that want them round-tripped.
    """

    workflow_id: uuid.UUID
    reason: str
    dag_snapshot: dict[str, Any]
    current_task_id: str | None = None
    completed_task_ids: tuple[str, ...] = ()
    pending_task_ids: tuple[str, ...] = ()
    failed_task_ids: tuple[str, ...] = ()
    variables: dict[str, Any] = field(default_factory=dict)
    reflection: dict[str, Any] | None = None
    version: int = CHECKPOINT_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    content_hash: str | None = None  # populated after CheckpointStore.save().

    @classmethod
    def from_dag(
        cls,
        *,
        workflow_id: uuid.UUID,
        dag: DAG,
        reason: str,
        current_task_id: str | None = None,
        variables: dict[str, Any] | None = None,
        reflection: dict[str, Any] | None = None,
    ) -> WorkflowCheckpoint:
        """Build a checkpoint from the live scheduler state."""
        snapshot = dag.to_dict()
        completed: list[str] = []
        pending: list[str] = []
        failed: list[str] = []
        for node in snapshot.get("nodes", []):
            status = str(node.get("status", ""))
            node_id = str(node.get("node_id", ""))
            if not node_id:
                continue
            if status == "completed":
                completed.append(node_id)
            elif status == "failed":
                failed.append(node_id)
            else:
                pending.append(node_id)
        return cls(
            workflow_id=workflow_id,
            reason=reason,
            dag_snapshot=snapshot,
            current_task_id=current_task_id,
            completed_task_ids=tuple(completed),
            pending_task_ids=tuple(pending),
            failed_task_ids=tuple(failed),
            variables=dict(variables or {}),
            reflection=dict(reflection) if reflection is not None else None,
        )

    def to_payload(self) -> dict[str, Any]:
        """Serialise to the dict form that gets embedded in memory metadata."""
        return {
            "version": self.version,
            "workflow_id": str(self.workflow_id),
            "reason": self.reason,
            "current_task_id": self.current_task_id,
            "completed_task_ids": list(self.completed_task_ids),
            "pending_task_ids": list(self.pending_task_ids),
            "failed_task_ids": list(self.failed_task_ids),
            "variables": dict(self.variables),
            "reflection": self.reflection,
            "dag_snapshot": self.dag_snapshot,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> WorkflowCheckpoint:
        """Rebuild from :meth:`to_payload` output."""
        try:
            workflow_id = uuid.UUID(str(payload["workflow_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointDecodeError(f"invalid/missing workflow_id: {exc}") from exc
        try:
            created_at = datetime.fromisoformat(str(payload.get("created_at", "")))
        except (TypeError, ValueError):
            created_at = datetime.now(UTC)
        return cls(
            workflow_id=workflow_id,
            reason=str(payload.get("reason", "")),
            dag_snapshot=dict(payload.get("dag_snapshot") or {}),
            current_task_id=payload.get("current_task_id"),
            completed_task_ids=tuple(payload.get("completed_task_ids") or ()),
            pending_task_ids=tuple(payload.get("pending_task_ids") or ()),
            failed_task_ids=tuple(payload.get("failed_task_ids") or ()),
            variables=dict(payload.get("variables") or {}),
            reflection=payload.get("reflection") if isinstance(payload.get("reflection"), dict) else None,
            version=int(payload.get("version") or CHECKPOINT_VERSION),
            created_at=created_at,
        )

    def restore_dag(self) -> DAG:
        """Rebuild a live :class:`DAG` object from the snapshot."""
        try:
            return DAG.from_dict(self.dag_snapshot)
        except Exception as exc:
            raise CheckpointDecodeError(f"cannot rebuild DAG from snapshot: {exc}") from exc


def _metadata_for(checkpoint: WorkflowCheckpoint) -> dict[str, Any]:
    """Shape the memory-record metadata that marks a row as a checkpoint."""
    return {
        CHECKPOINT_MARKER_KEY: True,
        CHECKPOINT_WORKFLOW_KEY: str(checkpoint.workflow_id),
        CHECKPOINT_REASON_KEY: checkpoint.reason,
        CHECKPOINT_PAYLOAD_KEY: checkpoint.to_payload(),
    }


def is_checkpoint_record(record: MemoryRecord) -> bool:
    """Return True if ``record`` is a persisted workflow checkpoint."""
    meta = record.metadata_ or {}
    return bool(meta.get(CHECKPOINT_MARKER_KEY)) and record.memory_type == MemoryType.META


class CheckpointStore:
    """Persist + retrieve :class:`WorkflowCheckpoint` via the CRDT store.

    The store is deliberately thin; all cryptographic guarantees come
    from :class:`CRDTStore` (signature verification, hash determinism,
    duplicate-hash idempotency). This class just maps the domain object
    to/from the canonical memory-record form.
    """

    def __init__(self, session: AsyncSession, keypair: KeyPair) -> None:
        self._session = session
        self._keypair = keypair
        self._author_did = build_did_key(keypair.public_bytes)
        self._store = CRDTStore(session=session)

    @property
    def author_did(self) -> str:
        return self._author_did

    async def save(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        """Persist the checkpoint and return a copy with ``content_hash`` populated.

        Duplicate-hash inserts collapse to a no-op (the existing row wins),
        so re-saving an identical checkpoint is cheap and safe.
        """
        content = (
            checkpoint.reason if checkpoint.reason else f"workflow checkpoint @ {checkpoint.created_at.isoformat()}"
        )
        record = await self._store.put(
            workflow_id=checkpoint.workflow_id,
            memory_type=MemoryType.META,
            content=content,
            confidence=1.0,
            metadata=_metadata_for(checkpoint),
            parent_hashes=[],
            author_did=self._author_did,
            keypair=self._keypair,
            record_state=RecordState.ACTIVE,
        )
        logger.info(
            "workflow_checkpoint_saved",
            workflow_id=str(checkpoint.workflow_id),
            reason=checkpoint.reason,
            hash=(record.content_hash or "")[:12],
            completed=len(checkpoint.completed_task_ids),
            pending=len(checkpoint.pending_task_ids),
        )
        return WorkflowCheckpoint(
            workflow_id=checkpoint.workflow_id,
            reason=checkpoint.reason,
            dag_snapshot=checkpoint.dag_snapshot,
            current_task_id=checkpoint.current_task_id,
            completed_task_ids=checkpoint.completed_task_ids,
            pending_task_ids=checkpoint.pending_task_ids,
            failed_task_ids=checkpoint.failed_task_ids,
            variables=checkpoint.variables,
            reflection=checkpoint.reflection,
            version=checkpoint.version,
            created_at=checkpoint.created_at,
            content_hash=record.content_hash,
        )

    async def load_by_hash(self, content_hash: str) -> WorkflowCheckpoint | None:
        """Fetch a checkpoint by its multibase content hash."""
        result = await self._session.execute(select(MemoryRecord).where(MemoryRecord.content_hash == content_hash))
        record = result.scalar_one_or_none()
        if record is None or not is_checkpoint_record(record):
            return None
        return _decode_record(record)

    async def load_latest(self, workflow_id: uuid.UUID) -> WorkflowCheckpoint | None:
        """Return the most recent checkpoint for a workflow, or ``None``."""
        result = await self._session.execute(
            select(MemoryRecord)
            .where(
                MemoryRecord.workflow_id == workflow_id,
                MemoryRecord.memory_type == MemoryType.META,
                MemoryRecord.record_state == RecordState.ACTIVE,
            )
            .order_by(MemoryRecord.created_at.desc())
        )
        for record in result.scalars().all():
            if is_checkpoint_record(record):
                return _decode_record(record)
        return None


def _decode_record(record: MemoryRecord) -> WorkflowCheckpoint:
    """Pull a :class:`WorkflowCheckpoint` out of a persisted memory record."""
    meta = record.metadata_ or {}
    payload = meta.get(CHECKPOINT_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        raise CheckpointDecodeError(f"memory record {record.content_hash!r} missing {CHECKPOINT_PAYLOAD_KEY}")
    checkpoint = WorkflowCheckpoint.from_payload(payload)
    return WorkflowCheckpoint(
        workflow_id=checkpoint.workflow_id,
        reason=checkpoint.reason,
        dag_snapshot=checkpoint.dag_snapshot,
        current_task_id=checkpoint.current_task_id,
        completed_task_ids=checkpoint.completed_task_ids,
        pending_task_ids=checkpoint.pending_task_ids,
        failed_task_ids=checkpoint.failed_task_ids,
        variables=checkpoint.variables,
        reflection=checkpoint.reflection,
        version=checkpoint.version,
        created_at=checkpoint.created_at,
        content_hash=record.content_hash,
    )
