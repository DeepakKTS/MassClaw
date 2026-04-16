"""CRDT synchronisation service — summary, bucket, fetch.

The three primitives the gossip loop will drive:

- :meth:`SyncService.summarise` — the Merkle fingerprint of this node's
  active memory state. Cheap: O(N) over hashes, cache-friendly.
- :meth:`SyncService.bucket_hashes` — the sorted list of content hashes
  in one bucket. Called only for buckets that differ between two peers,
  so traffic stays O(log N) per reconciliation round.
- :meth:`SyncService.fetch_records` — the full :class:`MemoryRecord` rows
  for a hand-picked set of hashes. Only signed records are served.

The service is deliberately thin. It knows nothing about HTTP, nothing
about authentication, nothing about peers — those concerns live in the
API layer (:mod:`app.api.memory_sync`) and :mod:`app.crdt.peer_auth`.

Phase 1 caches the summary in Redis with a short TTL (``_SUMMARY_CACHE_TTL_SECONDS``)
so every 5s-gossip-tick doesn't rebuild it from scratch. Cache invalidation
happens on every memory write via :meth:`invalidate_summary_cache`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.crdt.merkle import BUCKET_COUNT, MerkleSummary, summarise_hashes
from app.models.base import RecordState
from app.models.memory import MemoryRecord

logger = get_logger(__name__)

_SUMMARY_CACHE_KEY_FMT = "crdt:summary:{scope}"
_SUMMARY_CACHE_TTL_SECONDS = 30  # freshness vs rebuild-cost balance.
_MAX_FETCH_HASHES = 100  # safety cap on /sync/fetch request sizes.


class SyncServiceError(Exception):
    """Base class for sync service failures."""


class TooManyHashesRequested(SyncServiceError):  # noqa: N818 — describes the client-facing error
    """Raised when a client requests more hashes in one fetch than we allow."""


class SyncService:
    """Read-side federation helper — exposes summaries + record batches."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    # ---------------------------------------------------------------- Summary

    async def summarise(self, *, workflow_id: uuid.UUID | None = None) -> MerkleSummary:
        """Return the Merkle summary of this node's ACTIVE signed memory.

        ``workflow_id`` scopes the summary to a specific workflow. The
        cache key embeds the scope so instance-wide and per-workflow
        summaries never collide.
        """
        cache_key = _summary_cache_key(workflow_id)
        cached = await self._read_summary_cache(cache_key)
        if cached is not None:
            return cached

        hashes = [h async for h in self._iter_hashes(workflow_id=workflow_id)]
        summary = summarise_hashes(hashes)
        await self._write_summary_cache(cache_key, summary)
        return summary

    async def invalidate_summary_cache(self, *, workflow_id: uuid.UUID | None = None) -> None:
        """Drop the cached summary after a write.

        If ``workflow_id`` is given only that summary is cleared; the
        unscoped instance summary is always cleared because any write
        changes it too.
        """
        keys = [_summary_cache_key(None)]
        if workflow_id is not None:
            keys.append(_summary_cache_key(workflow_id))
        await self.redis.delete(*keys)

    # ---------------------------------------------------------------- Buckets

    async def bucket_hashes(
        self,
        bucket_index: int,
        *,
        workflow_id: uuid.UUID | None = None,
    ) -> list[str]:
        """Return the sorted, deduplicated hashes in one Merkle bucket."""
        if not 0 <= bucket_index < BUCKET_COUNT:
            raise ValueError(f"bucket index out of range: {bucket_index}")

        # Load all hashes (in practice this is already bounded by the
        # combined size of a workflow; instance-wide callers pay O(N)
        # for the one-off scan). A future optimisation is to store a
        # precomputed bucket index column.
        hashes: set[str] = set()
        async for h in self._iter_hashes(workflow_id=workflow_id):
            bucket = _bucket_for_hash_safe(h)
            if bucket == bucket_index:
                hashes.add(h)
        return sorted(hashes)

    # ---------------------------------------------------------------- Fetch

    async def fetch_records(self, hashes: Sequence[str]) -> list[MemoryRecord]:
        """Return every known record whose content hash is in ``hashes``.

        Tombstoned records are NOT served — the by-hash endpoint already
        documents this contract. Unknown hashes are silently omitted so a
        peer can ask optimistically without surfacing "not found" noise.
        """
        if not hashes:
            return []
        if len(hashes) > _MAX_FETCH_HASHES:
            raise TooManyHashesRequested(f"requested {len(hashes)} hashes; max per call is {_MAX_FETCH_HASHES}")

        result = await self.session.execute(
            select(MemoryRecord).where(
                MemoryRecord.content_hash.in_(hashes),
                MemoryRecord.record_state != RecordState.TOMBSTONED,
            )
        )
        return list(result.scalars().all())

    # ---------------------------------------------------------------- Internals

    async def _iter_hashes(
        self,
        *,
        workflow_id: uuid.UUID | None,
    ) -> Iterable[str]:
        """Yield every content hash of an ACTIVE signed record.

        Uses the ``ix_memory_records_workflow_state_created`` composite
        index when ``workflow_id`` is scoped; falls back to a plain
        ``record_state`` filter otherwise.
        """
        stmt = select(MemoryRecord.content_hash).where(
            MemoryRecord.content_hash.isnot(None),
            MemoryRecord.record_state == RecordState.ACTIVE,
        )
        if workflow_id is not None:
            stmt = stmt.where(MemoryRecord.workflow_id == workflow_id)
        result = await self.session.execute(stmt)
        for row in result.scalars().all():
            if row:
                yield row

    async def _read_summary_cache(self, key: str) -> MerkleSummary | None:
        try:
            raw = await self.redis.get(key)
        except Exception as exc:
            logger.warning("sync_summary_cache_read_failed", key=key, error=str(exc))
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return MerkleSummary(
                root=str(payload["root"]),
                buckets=list(payload["buckets"]),
                record_count=int(payload["record_count"]),
            )
        except Exception as exc:
            logger.warning("sync_summary_cache_decode_failed", key=key, error=str(exc))
            return None

    async def _write_summary_cache(self, key: str, summary: MerkleSummary) -> None:
        payload = json.dumps(
            {
                "root": summary.root,
                "buckets": summary.buckets,
                "record_count": summary.record_count,
            },
            separators=(",", ":"),
        )
        try:
            await self.redis.set(key, payload, ex=_SUMMARY_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("sync_summary_cache_write_failed", key=key, error=str(exc))


# ------------------------------------------------------------------ helpers


def _summary_cache_key(workflow_id: uuid.UUID | None) -> str:
    scope = "all" if workflow_id is None else f"wf-{workflow_id}"
    return _SUMMARY_CACHE_KEY_FMT.format(scope=scope)


def _bucket_for_hash_safe(content_hash: str) -> int:
    """Wrap :func:`bucket_for_hash` but treat malformed hashes as bucket -1."""
    from app.crdt.merkle import bucket_for_hash

    try:
        return bucket_for_hash(content_hash)
    except ValueError:
        return -1
