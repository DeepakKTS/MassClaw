"""Gossip loads only the local state a batch of peer records needs.

``_load_known_workflow_ids`` used to answer two membership questions — "do I
know this workflow?" and "do I already hold this hash?" — by loading *both
entire tables* into Python sets: every row of ``workflows`` plus every non-null
``content_hash`` in ``memory_records``. In demo mode the tick runs every five
seconds, so the cost was O(total records) of memory and transfer per round, on
a node that might need to check three hashes.

The fix is not a revert to one query per record (the docstring correctly warned
against that). It is to ask the bounded question: the records in hand name at
most ``_FETCH_BATCH_SIZE`` hashes and however many workflows, so both lookups
are scoped to those. Two queries per batch, regardless of table size.

These tests pin the bound *and* the two properties the old full-table load was
quietly providing, which a naive scoping would lose.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.crdt.gossip import GossipRoundReport, GossipService, _Known
from app.crdt.peer_client import PeerRecord
from app.crdt.store import CRDTStore
from app.crdt.sync import SyncService
from app.identity.did import build_did_key
from app.identity.signer import generate_keypair
from app.models.base import MemoryType, RecordState, WorkflowStatus
from app.models.memory import MemoryRecord
from app.models.workflow import Workflow


@pytest_asyncio.fixture
async def keypair():
    return generate_keypair()


@pytest_asyncio.fixture
async def gossip(db_session, redis_client) -> GossipService:
    return GossipService(
        session=db_session,
        sync_service=SyncService(session=db_session, redis=redis_client),
        crdt_store=CRDTStore(session=db_session),
    )


def _peer_record(keypair, workflow_id: uuid.UUID, content: str = "payload") -> PeerRecord:
    """Sign a record the way a peer node's gossip endpoint would serialize it."""
    from app.crdt.hashing import build_canonical_body, compute_content_hash
    from app.identity.signer import encode_multibase, sign_bytes

    author_did = build_did_key(keypair.public_bytes)
    body = build_canonical_body(
        workflow_id=workflow_id,
        memory_type=MemoryType.META,
        content=content,
        confidence=1.0,
        metadata={},
        parent_hashes=[],
        author_did=author_did,
        source_agent_id=None,
    )
    content_hash = compute_content_hash(body)
    signature = encode_multibase(sign_bytes(body.to_signable_bytes(), keypair.private_seed))
    return PeerRecord(
        content_hash=content_hash,
        signature=signature,
        author_did=author_did,
        workflow_id=str(workflow_id),
        source_agent_id=None,
        memory_type="meta",
        content=content,
        confidence=1.0,
        metadata={},
        parent_hashes=[],
        record_state="active",
        raw={},
    )


def _report() -> GossipRoundReport:
    return GossipRoundReport(
        peer_url="test",
        peer_did="test",
        duration_ms=0.0,
        buckets_compared=0,
        buckets_divergent=0,
        hashes_missing_locally=0,
        records_fetched=0,
        records_persisted=0,
    )


async def _workflow(session, **kwargs) -> Workflow:
    wf = Workflow(
        workflow_id=uuid.uuid4(),
        user_id="someone-else",
        prompt="an unrelated workflow",
        status=WorkflowStatus.RUNNING,
        budget_limit=100.0,
        **kwargs,
    )
    session.add(wf)
    await session.flush()
    return wf


class TestBoundedKnownState:
    @pytest.mark.asyncio
    async def test_only_the_workflows_named_by_the_batch_are_loaded(self, gossip, db_session, keypair):
        """Every other workflow on the node is irrelevant to this batch, and a
        federation node holds every workflow it has ever shadowed."""
        mine = await _workflow(db_session)
        for _ in range(3):
            await _workflow(db_session)

        known = _Known()
        await gossip._extend_known(known, [_peer_record(keypair, mine.workflow_id)])

        assert known.workflow_ids == {mine.workflow_id}

    @pytest.mark.asyncio
    async def test_only_the_hashes_named_by_the_batch_are_loaded(self, gossip, db_session, keypair):
        """The old load pulled every content_hash in memory_records — the table
        that grows fastest, and the one the cache used to fill without bound."""
        db_session.add(
            MemoryRecord(
                workflow_id=None,
                memory_type=MemoryType.FACT,
                content="an unrelated record we already hold",
                confidence=0.9,
                content_hash="zUnrelatedHashNobodyAskedAbout",
            )
        )
        await db_session.flush()

        record = _peer_record(keypair, (await _workflow(db_session)).workflow_id)
        known = _Known()
        await gossip._extend_known(known, [record])

        assert "zUnrelatedHashNobodyAskedAbout" not in known.content_hashes

    @pytest.mark.asyncio
    async def test_a_hash_we_hold_in_a_non_active_state_still_counts_as_a_duplicate(self, gossip, db_session, keypair):
        """This is why the hash lookup exists at all. Missing hashes are computed
        against the *active* bucket listing, so a record we hold as SUPERSEDED
        looks missing and comes back over the wire. Scoping the lookup must not
        start filtering by state, or every such record gets re-persisted and
        collides on the unique hash."""
        wf = await _workflow(db_session)
        record = _peer_record(keypair, wf.workflow_id)
        db_session.add(
            MemoryRecord(
                workflow_id=wf.workflow_id,
                memory_type=MemoryType.META,
                content="payload",
                confidence=1.0,
                content_hash=record.content_hash,
                record_state=RecordState.SUPERSEDED,
            )
        )
        await db_session.flush()

        known = _Known()
        await gossip._extend_known(known, [record])
        report = _report()

        assert await gossip._persist_peer_record(record, known, report) is False
        assert report.records_skipped_duplicate == 1

    @pytest.mark.asyncio
    async def test_a_hash_persisted_in_an_earlier_batch_is_not_re_persisted(self, gossip, db_session, keypair):
        """The old code loaded once per round, so anything persisted mid-round
        was already in the set. Per-batch loading has to preserve that."""
        wf = await _workflow(db_session)
        record = _peer_record(keypair, wf.workflow_id)

        known = _Known()
        await gossip._extend_known(known, [record])
        assert await gossip._persist_peer_record(record, known, _report()) is True

        # Second batch, same round, same record.
        await gossip._extend_known(known, [record])
        report = _report()
        assert await gossip._persist_peer_record(record, known, report) is False
        assert report.records_skipped_duplicate == 1

        stored = (
            (
                await db_session.execute(
                    select(MemoryRecord.memory_id).where(MemoryRecord.content_hash == record.content_hash)
                )
            )
            .scalars()
            .all()
        )
        assert len(stored) == 1

    @pytest.mark.asyncio
    async def test_records_naming_an_unparseable_workflow_do_not_break_the_load(self, gossip, keypair):
        """`_persist_peer_record` reports a bad workflow UUID as invalid; the
        bounded load runs first and must not raise on the same input."""
        record = _peer_record(keypair, uuid.uuid4())
        object.__setattr__(record, "workflow_id", "not-a-uuid")

        known = _Known()
        await gossip._extend_known(known, [record])

        assert known.workflow_ids == set()

        report = _report()
        assert await gossip._persist_peer_record(record, known, report) is False
        assert report.records_skipped_invalid == 1
