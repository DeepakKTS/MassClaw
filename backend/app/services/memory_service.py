from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.events import EventBus
from app.core.logging import get_logger
from app.crdt.store import CRDTStore
from app.embeddings.service import get_embedding_service
from app.exceptions import NotFoundError
from app.models.agent import Agent
from app.models.base import MemoryType, ReadMode, RecordState
from app.models.memory import MemoryRecord
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.memory import (
    FactResolutionRequest,
    FactResolutionResponse,
    MemoryQueryRequest,
    MemoryResponse,
    MemorySearchResult,
    MemoryWriteRequest,
    RankedCandidateOut,
)
from app.services.memory_fact_resolver import (
    FactCandidate,
    resolve_fact,
)
from app.services.memory_lifecycle import (
    GCRunPolicy,
    assert_can_tombstone,
    build_tombstone_metadata,
    decide_supersede,
    is_archive_candidate,
    tombstone_memory_type,
)

logger = get_logger(__name__)


class MemoryService:
    """Shared memory layer with vector semantic search, versioning, and conflict resolution.

    Key features:
    - Vector embeddings via pgvector for semantic retrieval
    - Memory version chains for full history
    - Freshness-weighted scoring: score = similarity * confidence * exp(-lambda * hours)
    - Conflict resolution: last-writer-wins weighted by confidence
    - Redis caching for query results
    - Garbage collection for expired / low-freshness records
    """

    CACHE_TTL_SECONDS = 60
    CACHE_PREFIX = "memory_cache:"
    CONFLICT_WINDOW_SECONDS = 5

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis
        self.settings = get_settings()
        self.embedding_service = get_embedding_service()

    async def write_memory(self, data: MemoryWriteRequest) -> MemoryRecord:
        """Write a memory record with embedding generation and version chain management.

        Steps:
        1. Generate vector embedding for content
        2. Handle version chain if parent_version_id provided
        3. Detect and resolve conflicts (same workflow + agent within conflict window)
        4. Insert record
        5. Invalidate relevant caches
        6. Publish event
        """
        # 1. Generate embedding
        embedding = await self.embedding_service.embed(data.content)

        # 2. Determine version number
        version = 1
        if data.parent_version_id:
            parent = await self._get_memory(data.parent_version_id)
            version = parent.version + 1

        # 3. Conflict resolution — check for recent writes from same agent to same workflow
        if data.source_agent_id:
            conflict = await self._check_conflict(
                workflow_id=data.workflow_id,
                source_agent_id=data.source_agent_id,
                memory_type=data.memory_type,
            )
            if conflict and data.confidence <= conflict.confidence:
                logger.info(
                    "memory_conflict_resolved_keep_existing",
                    existing_id=str(conflict.memory_id),
                    existing_confidence=conflict.confidence,
                    incoming_confidence=data.confidence,
                )
                # Return existing record — incoming has lower or equal confidence
                return conflict

        # 4. Compute expiry
        expires_at = None
        if data.ttl_hours:
            expires_at = datetime.now(UTC) + timedelta(hours=data.ttl_hours)

        # 5. Create record via the CRDT write path. When the caller supplies
        #    author_did + signature + hash, the store verifies the Ed25519
        #    signature and rejects any mismatch before persisting. When those
        #    fields are omitted the record is stored as a legacy unsigned row
        #    (backward compatible) — the content hash is still computed and
        #    stored when an author_did is declared, so content-addressed
        #    retrieval works for any author-anchored write.
        store = CRDTStore(self.session)
        record = await store.put(
            workflow_id=data.workflow_id,
            source_agent_id=data.source_agent_id,
            memory_type=data.memory_type,
            content=data.content,
            confidence=data.confidence,
            metadata=data.metadata,
            parent_hashes=data.parent_hashes,
            author_did=data.author_did,
            precomputed_hash=data.content_hash,
            precomputed_signature=data.signature,
            embedding=embedding,
            version=version,
            parent_version_id=data.parent_version_id,
            expires_at=expires_at,
        )

        # 6. Apply CRDT lifecycle rules — flip cited parents to SUPERSEDED
        #    when the new author's trust outweighs theirs by the policy delta.
        #    Only runs for signed records (unsigned records don't participate
        #    in federation-wide lifecycle because peers can't verify them).
        if record.is_signed:
            await self.apply_supersede_rules(new_record=record)

        # 7. Invalidate caches for this workflow
        await self._invalidate_cache(data.workflow_id)

        # 8. Publish event
        await EventBus.publish_dict(
            ["memory", str(data.workflow_id), "written"],
            "memory.written",
            {
                "memory_id": str(record.memory_id),
                "workflow_id": str(data.workflow_id),
                "source_agent_id": str(data.source_agent_id) if data.source_agent_id else None,
                "memory_type": data.memory_type.value,
                "version": version,
                "confidence": data.confidence,
            },
        )

        logger.info(
            "memory_written",
            memory_id=str(record.memory_id),
            workflow_id=str(data.workflow_id),
            memory_type=data.memory_type.value,
            version=version,
            content_length=len(data.content),
        )

        return record

    async def query_memory(self, query: MemoryQueryRequest) -> list[MemorySearchResult]:
        """Semantic search over memory records using pgvector cosine similarity.

        Scoring: relevance_score = similarity * confidence * freshness_factor
        Where freshness_factor = exp(-lambda * hours_since_creation)
        """
        # Check cache first
        cache_key = self._build_cache_key(query)
        cached = await self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Generate query embedding
        query_embedding = await self.embedding_service.embed(query.query)

        # Build the pgvector cosine similarity query
        # 1 - (embedding <=> query_embedding) gives cosine similarity [0, 1]
        similarity_expr = (1 - MemoryRecord.embedding.cosine_distance(query_embedding)).label("similarity")

        stmt = select(MemoryRecord, similarity_expr).where(MemoryRecord.embedding.isnot(None))

        # Apply filters
        conditions = []
        if query.workflow_id:
            conditions.append(MemoryRecord.workflow_id == query.workflow_id)
        if query.memory_types:
            conditions.append(MemoryRecord.memory_type.in_(query.memory_types))
        if query.min_confidence > 0:
            conditions.append(MemoryRecord.confidence >= query.min_confidence)
        # CRDT lifecycle filter. Defaults to [ACTIVE]; callers asking for the
        # audit view pass additional states explicitly.
        if query.include_states:
            conditions.append(MemoryRecord.record_state.in_(query.include_states))
        if query.author_did:
            conditions.append(MemoryRecord.author_did == query.author_did)

        # Exclude expired records
        now = datetime.now(UTC)
        conditions.append((MemoryRecord.expires_at.is_(None)) | (MemoryRecord.expires_at > now))

        if conditions:
            stmt = stmt.where(and_(*conditions))

        # Order by cosine similarity (ascending distance = descending similarity)
        stmt = stmt.order_by(MemoryRecord.embedding.cosine_distance(query_embedding))
        stmt = stmt.limit(query.top_k * 2)  # Fetch extra for post-filter

        result = await self.session.execute(stmt)
        rows = result.all()

        # Apply freshness weighting and minimum similarity filter
        decay_lambda = self.settings.memory_freshness_decay_lambda
        results: list[MemorySearchResult] = []

        for record, similarity in rows:
            if similarity < query.min_similarity:
                continue

            # Compute freshness factor
            created = record.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            hours_since = (now - created).total_seconds() / 3600.0
            freshness_factor = math.exp(-decay_lambda * hours_since)

            # Combined relevance score
            relevance = similarity * record.confidence * freshness_factor

            results.append(
                MemorySearchResult(
                    memory=MemoryResponse.model_validate(record),
                    similarity=round(similarity, 4),
                    relevance_score=round(relevance, 4),
                )
            )

        # Sort by relevance score and take top_k
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        results = results[: query.top_k]

        # Cache results
        await self._set_cached(cache_key, results)

        logger.info(
            "memory_queried",
            query_length=len(query.query),
            workflow_id=str(query.workflow_id) if query.workflow_id else None,
            results_count=len(results),
            top_similarity=results[0].similarity if results else 0,
        )

        return results

    async def resolve_fact(self, request: FactResolutionRequest) -> FactResolutionResponse:
        """Resolve a fact query under one of the three read modes.

        Loads candidates via the same semantic search as :meth:`query_memory`
        (so ``include_states`` / ``memory_types`` / ``workflow_id`` filters
        apply), enriches each with its author's current trust score, and
        hands them to the pure :func:`resolve_fact` resolver to pick the
        outcome. The resolver returns a winner for planning, every
        candidate for audit, and an HITL-escalation marker for sensitive
        mode when two candidates tie at high confidence.
        """
        # Reuse the query path for discovery — same similarity / confidence
        # / state filters apply. Audit mode should see every lifecycle state
        # the caller listed, while planning/sensitive are ACTIVE-only.
        include_states = list(request.include_states)
        if request.mode == ReadMode.AUDIT and not include_states:
            include_states = [RecordState.ACTIVE, RecordState.SUPERSEDED, RecordState.HISTORICAL]

        search_request = MemoryQueryRequest(
            query=request.subject,
            workflow_id=request.workflow_id,
            memory_types=request.memory_types,
            min_similarity=request.min_similarity,
            min_confidence=request.min_confidence,
            top_k=request.top_k,
            include_states=include_states,
        )
        search_results = await self.query_memory(search_request)

        if not search_results:
            return FactResolutionResponse(
                mode=request.mode,
                subject=request.subject,
                chosen=None,
                candidates=[],
                requires_hitl=False,
                conflict_detected=False,
                reason="no memory records matched the subject query",
            )

        # Fetch each author's trust score in one round-trip.
        agent_ids = {r.memory.source_agent_id for r in search_results if r.memory.source_agent_id is not None}
        trust_by_agent: dict[uuid.UUID, float] = {}
        if agent_ids:
            trust_rows = await self.session.execute(
                select(Agent.agent_id, Agent.trust_score).where(Agent.agent_id.in_(agent_ids))
            )
            trust_by_agent = {row[0]: float(row[1]) for row in trust_rows.all()}

        candidates = [
            FactCandidate(
                memory_id=r.memory.memory_id,
                content=r.memory.content,
                confidence=float(r.memory.confidence),
                author_did=r.memory.author_did,
                author_trust=(trust_by_agent.get(r.memory.source_agent_id) if r.memory.source_agent_id else None),
                record_state=r.memory.record_state,
                content_hash=r.memory.content_hash,
                created_at=r.memory.created_at,
                similarity=float(r.similarity),
            )
            for r in search_results
        ]

        resolution = resolve_fact(
            candidates,
            mode=request.mode,
            sensitive_min_confidence=request.sensitive_min_confidence,
            sensitive_rank_delta=request.sensitive_rank_delta,
        )

        memory_by_id = {r.memory.memory_id: r.memory for r in search_results}

        # Ranked candidate list for the response, in resolver-chosen order.
        ranked_out: list[RankedCandidateOut] = []
        similarity_by_id = {r.memory.memory_id: r.similarity for r in search_results}
        for ranked in resolution.candidates:
            cand = ranked.candidate
            memory = memory_by_id.get(cand.memory_id)
            if memory is None:
                continue
            ranked_out.append(
                RankedCandidateOut(
                    memory=memory,
                    rank=ranked.rank,
                    similarity=similarity_by_id.get(cand.memory_id, 0.0),
                    freshness_factor=ranked.freshness_factor,
                    effective_author_trust=ranked.effective_author_trust,
                )
            )

        chosen_out: MemoryResponse | None = None
        if resolution.chosen is not None:
            chosen_out = memory_by_id.get(resolution.chosen.memory_id)

        logger.info(
            "memory_fact_resolved",
            mode=request.mode.value,
            candidates=len(ranked_out),
            chosen=str(chosen_out.memory_id) if chosen_out else None,
            conflict_detected=resolution.conflict_detected,
            requires_hitl=resolution.requires_hitl,
        )

        return FactResolutionResponse(
            mode=resolution.mode,
            subject=request.subject,
            chosen=chosen_out,
            candidates=ranked_out,
            requires_hitl=resolution.requires_hitl,
            conflict_detected=resolution.conflict_detected,
            reason=resolution.reason,
        )

    async def get_workflow_memories(
        self,
        workflow_id: uuid.UUID,
        pagination: PaginationParams,
        memory_type: MemoryType | None = None,
    ) -> PaginatedResponse[MemoryResponse]:
        """Get all memory records for a workflow, paginated."""
        conditions = [MemoryRecord.workflow_id == workflow_id]
        if memory_type:
            conditions.append(MemoryRecord.memory_type == memory_type)

        # Count
        count_result = await self.session.execute(
            select(func.count()).select_from(MemoryRecord).where(and_(*conditions))
        )
        total = count_result.scalar_one()

        # Fetch
        result = await self.session.execute(
            select(MemoryRecord)
            .where(and_(*conditions))
            .order_by(MemoryRecord.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        records = list(result.scalars().all())

        return PaginatedResponse(
            items=[MemoryResponse.model_validate(r) for r in records],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def get_memory_versions(self, memory_id: uuid.UUID) -> list[MemoryResponse]:
        """Walk the version chain for a memory record, newest first."""
        versions: list[MemoryRecord] = []

        # Start from the given record and walk up via parent_version_id
        current_id: uuid.UUID | None = memory_id
        seen: set[uuid.UUID] = set()

        while current_id and current_id not in seen:
            seen.add(current_id)
            result = await self.session.execute(select(MemoryRecord).where(MemoryRecord.memory_id == current_id))
            record = result.scalar_one_or_none()
            if record is None:
                break
            versions.append(record)
            current_id = record.parent_version_id

        # Also look for children (newer versions) of the original record
        # Walk down: find records whose parent_version_id == memory_id
        child_id = memory_id
        child_seen: set[uuid.UUID] = set(seen)
        children: list[MemoryRecord] = []

        while True:
            result = await self.session.execute(
                select(MemoryRecord).where(
                    and_(
                        MemoryRecord.parent_version_id == child_id,
                        MemoryRecord.memory_id.notin_(child_seen),
                    )
                )
            )
            child = result.scalar_one_or_none()
            if child is None:
                break
            child_seen.add(child.memory_id)
            children.append(child)
            child_id = child.memory_id

        # Combine: children (newest) + [current] + parents (oldest)
        all_versions = list(reversed(children)) + versions
        return [MemoryResponse.model_validate(v) for v in all_versions]

    async def delete_memory(self, memory_id: uuid.UUID) -> None:
        """Soft-delete via lifecycle transition: ACTIVE/SUPERSEDED → TOMBSTONED.

        For unsigned legacy records we fall back to the old semantics
        (``confidence = 0.0``) so pre-v1.1 callers still work without the
        tombstone contract kicking in. Signed records go through the
        proper lifecycle so peers observe the tombstone during gossip.
        """
        result = await self.session.execute(select(MemoryRecord).where(MemoryRecord.memory_id == memory_id))
        record = result.scalar_one_or_none()
        if record is None:
            raise NotFoundError("MemoryRecord", str(memory_id))

        if record.is_signed:
            # Signed records must be tombstoned, not mutated. The admin
            # tombstone path here uses the record's own author DID so
            # peers see a tombstone written by the same party — any other
            # tombstone must go through :meth:`tombstone_memory` with a
            # caller-supplied keypair.
            record.record_state = RecordState.TOMBSTONED
        else:
            # Legacy unsigned record — old soft-delete semantics.
            record.confidence = 0.0
            record.record_state = RecordState.TOMBSTONED

        await self.session.flush()
        await self._invalidate_cache(record.workflow_id)

        logger.info(
            "memory_deleted",
            memory_id=str(memory_id),
            record_state=record.record_state.value,
            signed=record.is_signed,
        )

    async def tombstone_memory(
        self,
        target_hash: str,
        *,
        requesting_did: str,
        signature: str,
        content_hash: str,
        reason: str | None = None,
    ) -> MemoryRecord:
        """Write a signed tombstone record for ``target_hash`` and flip its state.

        This is the federation-correct deletion path: the caller supplies a
        signed tombstone record whose ``parent_hashes = [target_hash]`` and
        whose metadata carries the tombstone marker. We verify the
        signature via :class:`CRDTStore`, persist the tombstone, then flip
        the target record's ``record_state`` to ``TOMBSTONED`` so reads
        stop serving it. Peers learn about the tombstone through normal
        sync + their own tombstone-applying logic.
        """
        target = await self._load_by_hash(target_hash)
        if target is None:
            raise NotFoundError("MemoryRecord", f"hash={target_hash!r}")

        assert_can_tombstone(
            requesting_did=requesting_did,
            target_author_did=target.author_did,
            target_state=target.record_state,
        )

        tombstone_metadata = build_tombstone_metadata(target_hash=target_hash, reason=reason)
        store = CRDTStore(self.session)
        tombstone_record = await store.put(
            workflow_id=target.workflow_id,
            source_agent_id=target.source_agent_id,
            memory_type=tombstone_memory_type(),
            content=reason or "",
            confidence=1.0,
            metadata=tombstone_metadata,
            parent_hashes=[target_hash],
            author_did=requesting_did,
            precomputed_hash=content_hash,
            precomputed_signature=signature,
            record_state=RecordState.ACTIVE,  # the tombstone record itself is active.
        )

        # Flip the target record's state. We do NOT touch content — the
        # record remains in the DB so audit queries can find it, but the
        # /memory/by-hash and /memory/query surfaces filter it out.
        target.record_state = RecordState.TOMBSTONED
        await self.session.flush()

        await self._invalidate_cache(target.workflow_id)
        logger.info(
            "memory_tombstoned",
            target_hash=target_hash[:12],
            tombstone_hash=tombstone_record.content_hash[:12] if tombstone_record.content_hash else None,
            requesting_did=requesting_did,
        )
        return tombstone_record

    async def apply_supersede_rules(
        self,
        *,
        new_record: MemoryRecord,
    ) -> list[MemoryRecord]:
        """Flip cited parents to SUPERSEDED when the new record's author outweighs them.

        Called from the write path after a new record lands. Parents cited
        in ``new_record.parent_hashes`` are inspected and, where the trust
        delta is met, their ``record_state`` is transitioned. Returns the
        list of records actually flipped, so the caller can log / emit
        events.
        """
        if not new_record.parent_hashes or not new_record.author_did:
            return []

        from sqlalchemy import select as _select

        parent_rows_result = await self.session.execute(
            _select(MemoryRecord).where(MemoryRecord.content_hash.in_(new_record.parent_hashes))
        )
        parent_rows = list(parent_rows_result.scalars().all())
        if not parent_rows:
            return []

        new_author_trust = await self._author_trust(new_record.source_agent_id)
        parent_trust_by_hash: dict[str, float | None] = {}
        for parent in parent_rows:
            if parent.content_hash is None:
                continue
            parent_trust_by_hash[parent.content_hash] = await self._author_trust(parent.source_agent_id)

        decision = decide_supersede(
            new_author_trust=new_author_trust,
            new_parent_hashes=list(new_record.parent_hashes),
            parent_author_trusts=parent_trust_by_hash,
        )
        if not decision.supersede:
            return []

        flipped: list[MemoryRecord] = []
        for parent in parent_rows:
            if parent.content_hash not in decision.target_hashes:
                continue
            if parent.record_state == RecordState.ACTIVE:
                parent.record_state = RecordState.SUPERSEDED
                flipped.append(parent)

        if flipped:
            await self.session.flush()
            logger.info(
                "memory_records_superseded",
                count=len(flipped),
                by_hash=(new_record.content_hash or "")[:12],
                reason=decision.reason,
            )
        return flipped

    async def garbage_collect(
        self,
        policy: GCRunPolicy | None = None,
        max_age_hours: int | None = None,
        min_confidence: float = 0.01,
    ) -> dict[str, int]:
        """Lifecycle-aware garbage collection.

        Pipeline on every run:

        1. ACTIVE/SUPERSEDED records whose ``expires_at < now`` → TOMBSTONED.
        2. Tombstoned records older than ``tombstone_grace_hours`` → hard deleted.
        3. ACTIVE/SUPERSEDED records whose age exceeds
           ``archive_active_after_hours`` and whose confidence is below
           ``low_confidence_threshold`` → HISTORICAL.
        4. Legacy unsigned records with ``confidence < low_confidence_threshold``
           → hard deleted (back-compat with the pre-lifecycle behaviour).
        5. Optional: hard-delete anything older than ``max_age_hours``
           (disabled by default; operator opt-in).

        Returns per-category counts. Signed records are *never* hard-
        deleted without first passing through TOMBSTONED, so peers always
        observe the lifecycle transition through normal sync.
        """
        run_policy = policy or GCRunPolicy(
            low_confidence_threshold=min_confidence,
            hard_delete_max_age_hours=max_age_hours,
        )
        now = datetime.now(UTC)
        counts = {
            "expired_to_tombstone": 0,
            "tombstone_hard_deleted": 0,
            "archived_to_historical": 0,
            "legacy_hard_deleted": 0,
            "old_hard_deleted": 0,
            "total_hard_deleted": 0,
        }

        # 1. Expire → tombstone (signed records) OR hard-delete (unsigned).
        #    LIMIT caps a single GC run so a big backlog can't OOM the
        #    worker — the next Celery tick finishes what's left.
        _GC_BATCH = 10_000
        expired_stmt = (
            select(MemoryRecord)
            .where(
                and_(
                    MemoryRecord.expires_at.isnot(None),
                    MemoryRecord.expires_at < now,
                    MemoryRecord.record_state.in_([RecordState.ACTIVE, RecordState.SUPERSEDED]),
                )
            )
            .limit(_GC_BATCH)
        )
        for row in (await self.session.execute(expired_stmt)).scalars().all():
            row.record_state = RecordState.TOMBSTONED
            counts["expired_to_tombstone"] += 1

        # 2. Hard-delete tombstones past the grace period.
        tombstone_cutoff = run_policy.tombstone_cutoff(now=now)
        gc_result = await self.session.execute(
            delete(MemoryRecord)
            .where(
                and_(
                    MemoryRecord.record_state == RecordState.TOMBSTONED,
                    MemoryRecord.created_at < tombstone_cutoff,
                )
            )
            .returning(MemoryRecord.memory_id)
        )
        counts["tombstone_hard_deleted"] = len(gc_result.all())

        # 3. Archive old + low-confidence ACTIVE/SUPERSEDED records.
        archive_cutoff = run_policy.archive_cutoff(now=now)
        archive_stmt = (
            select(MemoryRecord)
            .where(
                and_(
                    MemoryRecord.record_state.in_([RecordState.ACTIVE, RecordState.SUPERSEDED]),
                    MemoryRecord.created_at < archive_cutoff,
                    MemoryRecord.confidence < run_policy.low_confidence_threshold,
                )
            )
            .limit(_GC_BATCH)
        )
        for row in (await self.session.execute(archive_stmt)).scalars().all():
            if is_archive_candidate(
                record_state=row.record_state,
                created_at=row.created_at,
                now=now,
                archive_after_hours=run_policy.archive_active_after_hours,
            ):
                row.record_state = RecordState.HISTORICAL
                counts["archived_to_historical"] += 1

        # 4. Back-compat: legacy unsigned records with very low confidence
        #    still get hard-deleted (these pre-date the lifecycle contract).
        legacy_result = await self.session.execute(
            delete(MemoryRecord)
            .where(
                and_(
                    MemoryRecord.confidence < run_policy.low_confidence_threshold,
                    MemoryRecord.content_hash.is_(None),
                    MemoryRecord.record_state != RecordState.TOMBSTONED,
                )
            )
            .returning(MemoryRecord.memory_id)
        )
        counts["legacy_hard_deleted"] = len(legacy_result.all())

        # 5. Operator-opt-in max-age hard delete. Only affects unsigned
        #    records to keep the federation contract intact.
        if run_policy.hard_delete_max_age_hours:
            cutoff = now - timedelta(hours=run_policy.hard_delete_max_age_hours)
            old_result = await self.session.execute(
                delete(MemoryRecord)
                .where(
                    and_(
                        MemoryRecord.created_at < cutoff,
                        MemoryRecord.content_hash.is_(None),
                    )
                )
                .returning(MemoryRecord.memory_id)
            )
            counts["old_hard_deleted"] = len(old_result.all())

        counts["total_hard_deleted"] = (
            counts["tombstone_hard_deleted"] + counts["legacy_hard_deleted"] + counts["old_hard_deleted"]
        )

        await self.session.flush()
        # Emit log only when something actually happened — keeps the
        # memory-gc-every-6h schedule quiet during idle periods.
        any_change = any(v for v in counts.values() if isinstance(v, int))
        if any_change:
            logger.info("memory_gc_completed", **counts)
        return counts

    async def _load_by_hash(self, content_hash: str) -> MemoryRecord | None:
        result = await self.session.execute(select(MemoryRecord).where(MemoryRecord.content_hash == content_hash))
        return result.scalar_one_or_none()

    async def _author_trust(self, agent_id: uuid.UUID | None) -> float | None:
        if agent_id is None:
            return None
        trust_row = await self.session.execute(select(Agent.trust_score).where(Agent.agent_id == agent_id))
        value = trust_row.scalar_one_or_none()
        return float(value) if value is not None else None

    # --- Private helpers ---

    async def _get_memory(self, memory_id: uuid.UUID) -> MemoryRecord:
        result = await self.session.execute(select(MemoryRecord).where(MemoryRecord.memory_id == memory_id))
        record = result.scalar_one_or_none()
        if record is None:
            raise NotFoundError("MemoryRecord", str(memory_id))
        return record

    async def _check_conflict(
        self,
        workflow_id: uuid.UUID,
        source_agent_id: uuid.UUID,
        memory_type: MemoryType,
    ) -> MemoryRecord | None:
        """Check for a recent conflicting write (same workflow + agent + type within window)."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self.CONFLICT_WINDOW_SECONDS)
        result = await self.session.execute(
            select(MemoryRecord)
            .where(
                and_(
                    MemoryRecord.workflow_id == workflow_id,
                    MemoryRecord.source_agent_id == source_agent_id,
                    MemoryRecord.memory_type == memory_type,
                    MemoryRecord.created_at >= cutoff,
                )
            )
            .order_by(MemoryRecord.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _build_cache_key(self, query: MemoryQueryRequest) -> str:
        """Build a deterministic cache key from query parameters."""
        key_data = {
            "q": query.query,
            "wf": str(query.workflow_id) if query.workflow_id else None,
            "types": sorted([t.value for t in query.memory_types]) if query.memory_types else None,
            "sim": query.min_similarity,
            "conf": query.min_confidence,
            "k": query.top_k,
        }
        key_hash = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()[:16]
        return f"{self.CACHE_PREFIX}{key_hash}"

    async def _get_cached(self, cache_key: str) -> list[MemorySearchResult] | None:
        """Retrieve cached query results."""
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [MemorySearchResult.model_validate(item) for item in data]
        except Exception as e:
            logger.warning("memory_cache_read_error", error=str(e))
        return None

    async def _set_cached(self, cache_key: str, results: list[MemorySearchResult]) -> None:
        """Cache query results with TTL."""
        try:
            data = [r.model_dump(mode="json") for r in results]
            await self.redis.set(
                cache_key,
                json.dumps(data, default=str),
                ex=self.CACHE_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("memory_cache_write_error", error=str(e))

    async def _invalidate_cache(self, workflow_id: uuid.UUID) -> None:
        """Invalidate all cached queries. Uses prefix scan for targeted invalidation."""
        try:
            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(cursor=cursor, match=f"{self.CACHE_PREFIX}*", count=100)
                if keys:
                    await self.redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("memory_cache_invalidate_error", error=str(e))
