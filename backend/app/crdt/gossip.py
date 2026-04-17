"""Gossip orchestrator — reconciles two MassClaw nodes over the sync protocol.

One :class:`GossipService` instance runs one reconciliation round with one
peer. The protocol:

1. Fetch the peer's Merkle summary.
2. Compute our local summary (cached in Redis).
3. Diff → the set of bucket indices where we and the peer disagree.
4. For each differing bucket, fetch the peer's sorted hash list and
   subtract the hashes we already have locally.
5. Fetch the missing records in batches of ≤100.
6. Verify each record via the CRDT store (signature + canonical-hash
   match) and persist the ones we can accept. Records whose
   ``workflow_id`` is unknown to this node are skipped — CRDT federation
   of workflow rows themselves is a post-Phase-1 concern.

The orchestrator never mutates peer state; it is a pull-only read side.
The :meth:`reconcile_with` method returns a :class:`GossipRoundReport`
with the counts a caller (or Celery task) needs to log + expose as
metrics.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.crdt.peer_client import (
    PeerClient,
    PeerClientError,
    PeerRecord,
)
from app.crdt.store import CRDTStore, CRDTStoreError, SignatureMismatchError
from app.crdt.sync import SyncService
from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord
from app.models.workflow import Workflow

logger = get_logger(__name__)

_FETCH_BATCH_SIZE = 100  # mirror the server's /sync/fetch cap.


@dataclass
class GossipRoundReport:
    """Per-round stats returned by :meth:`GossipService.reconcile_with`."""

    peer_url: str
    peer_did: str
    duration_ms: float
    buckets_compared: int
    buckets_divergent: int
    hashes_missing_locally: int
    records_fetched: int
    records_persisted: int
    records_skipped_unknown_workflow: int = 0
    records_skipped_invalid: int = 0
    records_skipped_duplicate: int = 0
    error: str | None = None
    in_sync_before: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None

    def as_log_fields(self) -> dict:
        return {
            "peer_url": self.peer_url,
            "peer_did": self.peer_did,
            "duration_ms": round(self.duration_ms, 1),
            "in_sync_before": self.in_sync_before,
            "buckets_divergent": self.buckets_divergent,
            "records_fetched": self.records_fetched,
            "records_persisted": self.records_persisted,
            "skipped_unknown_workflow": self.records_skipped_unknown_workflow,
            "skipped_invalid": self.records_skipped_invalid,
            "skipped_duplicate": self.records_skipped_duplicate,
            "error": self.error,
        }


@dataclass
class _Known:
    """Local accounting during a reconciliation round."""

    workflow_ids: set[uuid.UUID] = field(default_factory=set)
    content_hashes: set[str] = field(default_factory=set)


class GossipService:
    """Drive a single reconciliation round with one peer."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        sync_service: SyncService,
        crdt_store: CRDTStore,
    ) -> None:
        self._session = session
        self._sync = sync_service
        self._store = crdt_store

    async def reconcile_with(
        self,
        peer: PeerClient,
        *,
        workflow_id: uuid.UUID | None = None,
    ) -> GossipRoundReport:
        """Run one complete summary→diff→fetch→persist round with ``peer``.

        Never raises: all failures are captured in the returned report's
        ``error`` field. This is what the Celery task wants — one bad peer
        should not crash the worker.
        """
        started = time.monotonic()
        report = GossipRoundReport(
            peer_url=peer.base_url,
            peer_did=peer.our_did,
            duration_ms=0.0,
            buckets_compared=0,
            buckets_divergent=0,
            hashes_missing_locally=0,
            records_fetched=0,
            records_persisted=0,
        )

        try:
            local_summary = await self._sync.summarise(workflow_id=workflow_id)
            peer_summary = await peer.fetch_summary(workflow_id=workflow_id)
            report.buckets_compared = len(local_summary.buckets)

            if local_summary.root == peer_summary.root:
                report.in_sync_before = True
                report.duration_ms = (time.monotonic() - started) * 1000.0
                return report

            divergent = local_summary.diverges_from(peer_summary)
            report.buckets_divergent = len(divergent)
            if not divergent:
                report.in_sync_before = True
                report.duration_ms = (time.monotonic() - started) * 1000.0
                return report

            missing_hashes = await self._collect_missing_hashes(
                peer=peer,
                bucket_indices=divergent,
                workflow_id=workflow_id,
            )
            report.hashes_missing_locally = len(missing_hashes)
            if not missing_hashes:
                report.duration_ms = (time.monotonic() - started) * 1000.0
                return report

            known = await self._load_known_workflow_ids()

            for batch in _chunks(missing_hashes, _FETCH_BATCH_SIZE):
                records = await peer.fetch_records(batch)
                report.records_fetched += len(records)
                for record in records:
                    persisted = await self._persist_peer_record(record, known, report)
                    if persisted:
                        report.records_persisted += 1

        except PeerClientError as exc:
            report.error = f"peer_client_error: {exc}"
            logger.warning("gossip_peer_error", **report.as_log_fields())
        except Exception as exc:
            report.error = f"unexpected_error: {type(exc).__name__}: {exc}"
            logger.exception("gossip_unexpected_error", **report.as_log_fields())

        report.duration_ms = (time.monotonic() - started) * 1000.0
        return report

    # ---------------------------------------------------------------- Helpers

    async def _collect_missing_hashes(
        self,
        *,
        peer: PeerClient,
        bucket_indices: list[int],
        workflow_id: uuid.UUID | None,
    ) -> list[str]:
        """For each divergent bucket, pull peer hashes and subtract ours."""
        missing: list[str] = []
        for idx in bucket_indices:
            peer_hashes = await peer.fetch_bucket_hashes(idx, workflow_id=workflow_id)
            if not peer_hashes:
                continue
            local_hashes = set(await self._sync.bucket_hashes(idx, workflow_id=workflow_id))
            for h in peer_hashes:
                if h not in local_hashes:
                    missing.append(h)
        return missing

    async def _load_known_workflow_ids(self) -> _Known:
        """Load the set of workflow IDs this node knows about.

        The FK constraint on ``memory_records.workflow_id`` means we cannot
        persist a peer's record if the corresponding workflow row is absent
        here. Loading the set once per round keeps the per-record check fast.
        """
        known = _Known()
        result = await self._session.execute(select(Workflow.workflow_id))
        for row in result.scalars().all():
            known.workflow_ids.add(row)
        # Pre-load the hashes we already have so we never re-persist.
        hash_result = await self._session.execute(
            select(MemoryRecord.content_hash).where(MemoryRecord.content_hash.isnot(None))
        )
        for row in hash_result.scalars().all():
            if row:
                known.content_hashes.add(row)
        return known

    async def _persist_peer_record(
        self,
        record: PeerRecord,
        known: _Known,
        report: GossipRoundReport,
    ) -> bool:
        """Verify + persist one peer-supplied record. Returns True on success."""
        try:
            workflow_uuid = uuid.UUID(record.workflow_id) if record.workflow_id else None
        except (TypeError, ValueError):
            report.records_skipped_invalid += 1
            logger.warning(
                "gossip_record_invalid_workflow_uuid",
                hash=record.content_hash[:12],
                raw=record.workflow_id,
            )
            return False

        if workflow_uuid is None or workflow_uuid not in known.workflow_ids:
            report.records_skipped_unknown_workflow += 1
            logger.info(
                "gossip_record_skipped_unknown_workflow",
                hash=record.content_hash[:12],
                workflow_id=str(workflow_uuid) if workflow_uuid else None,
            )
            return False

        if record.content_hash in known.content_hashes:
            report.records_skipped_duplicate += 1
            return False

        source_agent_uuid: uuid.UUID | None = None
        if record.source_agent_id:
            try:
                source_agent_uuid = uuid.UUID(record.source_agent_id)
            except (TypeError, ValueError):
                report.records_skipped_invalid += 1
                logger.warning(
                    "gossip_record_invalid_agent_uuid",
                    hash=record.content_hash[:12],
                    raw=record.source_agent_id,
                )
                return False

        try:
            memory_type = MemoryType(record.memory_type)
        except ValueError:
            report.records_skipped_invalid += 1
            logger.warning(
                "gossip_record_invalid_memory_type",
                hash=record.content_hash[:12],
                raw=record.memory_type,
            )
            return False

        try:
            record_state = RecordState(record.record_state)
        except ValueError:
            record_state = RecordState.ACTIVE  # default if peer reports something unknown.

        try:
            await self._store.put(
                workflow_id=workflow_uuid,
                source_agent_id=source_agent_uuid,
                memory_type=memory_type,
                content=record.content,
                confidence=record.confidence,
                metadata=record.metadata,
                parent_hashes=record.parent_hashes,
                author_did=record.author_did,
                precomputed_hash=record.content_hash,
                precomputed_signature=record.signature,
                record_state=record_state,
            )
        except SignatureMismatchError as exc:
            report.records_skipped_invalid += 1
            logger.warning(
                "gossip_record_signature_invalid",
                hash=record.content_hash[:12],
                error=str(exc),
            )
            return False
        except CRDTStoreError as exc:
            report.records_skipped_invalid += 1
            logger.warning(
                "gossip_record_store_error",
                hash=record.content_hash[:12],
                error=str(exc),
            )
            return False

        known.content_hashes.add(record.content_hash)
        return True


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
