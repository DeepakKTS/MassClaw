"""Garbage collection of semantic cache entries.

The cache lives in ``memory_records`` but is not a CRDT record: entries are
unsigned (``content_hash IS NULL``), so no peer can ever advertise or fetch
one, and they never need the tombstone → grace → delete dance that exists so
peers observe a lifecycle transition. They are therefore collected on their
own terms — expired entries deleted outright, and the live set capped.

``MemoryService.garbage_collect`` had no tests at all before this file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord
from app.services.memory_lifecycle import GCRunPolicy
from app.services.memory_service import MemoryService


class TestSemanticCacheGarbageCollection:
    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client) -> MemoryService:
        # Other tests commit cache-shaped rows through their own sessions, and
        # the cap below is global by definition, so start from a clean slate.
        # Rolled back with the rest of the test transaction.
        await db_session.execute(delete(MemoryRecord).where(MemoryRecord.memory_type == MemoryType.CACHE))
        return MemoryService(db_session, redis_client)

    @staticmethod
    def _cache_row(
        *,
        content: str,
        age_minutes: int = 0,
        ttl_hours: int | None = 24,
    ) -> MemoryRecord:
        now = datetime.now(UTC)
        return MemoryRecord(
            workflow_id=None,
            source_agent_id=None,
            memory_type=MemoryType.CACHE,
            content=content,
            embedding=[0.1] * 384,
            confidence=0.85,
            created_at=now - timedelta(minutes=age_minutes),
            expires_at=None if ttl_hours is None else now + timedelta(hours=ttl_hours),
            metadata_={"capability": "research", "cache_version": 1},
        )

    async def _live_cache_contents(self, session) -> list[str]:
        rows = (
            (
                await session.execute(
                    select(MemoryRecord.content)
                    .where(MemoryRecord.memory_type == MemoryType.CACHE)
                    .order_by(MemoryRecord.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    @pytest.mark.asyncio
    async def test_expired_entries_are_deleted_outright_not_tombstoned(self, service):
        """An expired entry is dead weight, not history: nothing can fetch it by
        hash, so parking it in TOMBSTONED for the grace period just keeps a
        stale embedding in the ivfflat index."""
        service.session.add(self._cache_row(content="expired entry", ttl_hours=-1))
        await service.session.flush()

        counts = await service.garbage_collect()

        assert counts["cache_expired_deleted"] == 1
        assert counts["expired_to_tombstone"] == 0
        assert await self._live_cache_contents(service.session) == []

    @pytest.mark.asyncio
    async def test_entries_beyond_the_cap_are_trimmed_oldest_first(self, service):
        """TTL alone cannot bound the table: a busy instance can write far more
        than the cap between two sweeps, and every entry is valid for 24h."""
        for age in (0, 10, 20, 30, 40):
            service.session.add(self._cache_row(content=f"entry aged {age}", age_minutes=age))
        await service.session.flush()

        counts = await service.garbage_collect(policy=GCRunPolicy(cache_max_entries=3))

        assert counts["cache_trimmed"] == 2
        assert await self._live_cache_contents(service.session) == [
            "entry aged 0",
            "entry aged 10",
            "entry aged 20",
        ]

    @pytest.mark.asyncio
    async def test_trim_leaves_real_memory_records_alone(self, service, sample_workflow):
        """The cap counts cache entries only — a workflow's own task output is
        not a candidate for eviction no matter how many entries exist."""
        for age in (0, 10, 20):
            service.session.add(self._cache_row(content=f"entry aged {age}", age_minutes=age))
        service.session.add(
            MemoryRecord(
                workflow_id=sample_workflow.workflow_id,
                source_agent_id=None,
                memory_type=MemoryType.RESULT,
                content="a genuine task result",
                embedding=[0.1] * 384,
                confidence=0.85,
            )
        )
        await service.session.flush()

        counts = await service.garbage_collect(policy=GCRunPolicy(cache_max_entries=1))

        assert counts["cache_trimmed"] == 2
        survivors = (
            (
                await service.session.execute(
                    select(MemoryRecord.content).where(MemoryRecord.memory_type == MemoryType.RESULT)
                )
            )
            .scalars()
            .all()
        )
        assert "a genuine task result" in survivors

    @pytest.mark.asyncio
    async def test_no_op_when_under_the_cap_and_unexpired(self, service):
        service.session.add(self._cache_row(content="a live entry"))
        await service.session.flush()

        counts = await service.garbage_collect(policy=GCRunPolicy(cache_max_entries=10))

        assert counts["cache_expired_deleted"] == 0
        assert counts["cache_trimmed"] == 0
        assert await self._live_cache_contents(service.session) == ["a live entry"]

    @pytest.mark.asyncio
    async def test_entry_without_an_expiry_is_collected(self, service):
        """An entry with no expires_at can never be evicted by TTL — that is
        exactly the unbounded growth this pass removed, so treat it as
        malformed and collect it rather than letting it live forever."""
        service.session.add(self._cache_row(content="no expiry at all", ttl_hours=None))
        await service.session.flush()

        counts = await service.garbage_collect()

        assert counts["cache_expired_deleted"] == 1
        assert await self._live_cache_contents(service.session) == []

    @pytest.mark.asyncio
    async def test_tombstoned_entries_count_against_nothing_and_are_collected(self, service):
        """A cache entry tombstoned by hand (or by an older GC run) is collected
        on the next sweep rather than lingering until its TTL expires."""
        row = self._cache_row(content="tombstoned entry")
        row.record_state = RecordState.TOMBSTONED
        service.session.add(row)
        await service.session.flush()

        counts = await service.garbage_collect()

        assert counts["cache_expired_deleted"] == 1
        assert await self._live_cache_contents(service.session) == []
