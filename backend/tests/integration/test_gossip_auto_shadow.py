"""Gossip auto-shadow tests: peer records for unknown workflows must persist.

The HITL cross-node flow requires a peer node to see an approval request
that was created on the originating node. Before this change, the peer
dropped those records because their ``workflow_id`` FK had nothing to
resolve against locally. The fix auto-creates a minimal ``Workflow``
shadow row so the record persists; :class:`WorkflowResumer` later fills
in the missing fields when a human triggers ``/workflows/{id}/resume``.
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
async def originator_keypair():
    # The keypair used by the "peer" node that authored the record.
    return generate_keypair()


def _make_peer_record(keypair, workflow_id: uuid.UUID, content: str = "payload") -> PeerRecord:
    """Sign a record the way a peer node's gossip endpoint would serialize it.

    We piggy-back on ``CRDTStore`` to compute hash + signature, then pack
    those fields into a ``PeerRecord`` so the gossip ingest path gets
    exactly what it would receive over HTTP.
    """
    from app.crdt.hashing import build_canonical_body, compute_content_hash
    from app.identity.signer import encode_multibase, sign_bytes

    author_did = build_did_key(keypair.public_bytes)
    body = build_canonical_body(
        workflow_id=workflow_id,
        memory_type=MemoryType.META,
        content=content,
        confidence=1.0,
        metadata={"approval_marker": True, "approval_status": "pending"},
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
        metadata={"approval_marker": True, "approval_status": "pending"},
        parent_hashes=[],
        record_state="active",
        raw={},
    )


class TestAutoShadow:
    @pytest.mark.asyncio
    async def test_unknown_workflow_is_shadowed_and_record_persists(self, db_session, redis_client, originator_keypair):
        """A record whose workflow_id doesn't exist locally should cause
        the gossip ingest path to create a shadow Workflow row, then
        persist the record."""
        workflow_id = uuid.uuid4()
        # Confirm the workflow does NOT exist yet on this node.
        existing = await db_session.execute(select(Workflow.workflow_id).where(Workflow.workflow_id == workflow_id))
        assert existing.scalar_one_or_none() is None

        record = _make_peer_record(originator_keypair, workflow_id)

        sync = SyncService(session=db_session, redis=redis_client)
        store = CRDTStore(session=db_session)
        gossip = GossipService(session=db_session, sync_service=sync, crdt_store=store)

        known = _Known()
        await gossip._extend_known(known, [record])
        report = GossipRoundReport(
            peer_url="test",
            peer_did="test",
            duration_ms=0.0,
            buckets_compared=0,
            buckets_divergent=0,
            hashes_missing_locally=0,
            records_fetched=0,
            records_persisted=0,
        )

        ok = await gossip._persist_peer_record(record, known, report)
        assert ok, "record should persist after auto-shadow"

        # The shadow workflow row exists now.
        shadow = (await db_session.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))).scalar_one()
        assert shadow.status == WorkflowStatus.PAUSED
        assert shadow.user_id == "federation-gossip"

        # The record was persisted.
        record_count = (
            (await db_session.execute(select(MemoryRecord).where(MemoryRecord.workflow_id == workflow_id)))
            .scalars()
            .all()
        )
        assert len(record_count) == 1
        assert record_count[0].memory_type == MemoryType.META
        assert report.records_skipped_unknown_workflow == 0

    @pytest.mark.asyncio
    async def test_known_workflow_no_shadow_created(self, db_session, redis_client, originator_keypair):
        """A record whose workflow already exists must NOT create a shadow
        (we don't want to overwrite the real workflow's user_id / prompt)."""
        workflow_id = uuid.uuid4()
        real_wf = Workflow(
            workflow_id=workflow_id,
            user_id="real-user",
            prompt="real prompt",
            status=WorkflowStatus.RUNNING,
            budget_limit=500.0,
        )
        db_session.add(real_wf)
        await db_session.flush()

        record = _make_peer_record(originator_keypair, workflow_id)

        sync = SyncService(session=db_session, redis=redis_client)
        store = CRDTStore(session=db_session)
        gossip = GossipService(session=db_session, sync_service=sync, crdt_store=store)

        known = _Known()
        await gossip._extend_known(known, [record])
        report = GossipRoundReport(
            peer_url="test",
            peer_did="test",
            duration_ms=0.0,
            buckets_compared=0,
            buckets_divergent=0,
            hashes_missing_locally=0,
            records_fetched=0,
            records_persisted=0,
        )

        ok = await gossip._persist_peer_record(record, known, report)
        assert ok

        # The workflow's original fields are unchanged.
        refreshed = (await db_session.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))).scalar_one()
        assert refreshed.user_id == "real-user"
        assert refreshed.prompt == "real prompt"
        assert refreshed.status == WorkflowStatus.RUNNING

    @pytest.mark.asyncio
    async def test_auto_shadow_is_idempotent(self, db_session, redis_client, originator_keypair):
        """Two concurrent gossip ticks both encountering the same unknown
        workflow shouldn't crash; the second caller detects the row
        already exists."""
        workflow_id = uuid.uuid4()
        record = _make_peer_record(originator_keypair, workflow_id)

        sync = SyncService(session=db_session, redis=redis_client)
        store = CRDTStore(session=db_session)
        gossip = GossipService(session=db_session, sync_service=sync, crdt_store=store)

        known = _Known()
        await gossip._extend_known(known, [record])
        report = GossipRoundReport(
            peer_url="test",
            peer_did="test",
            duration_ms=0.0,
            buckets_compared=0,
            buckets_divergent=0,
            hashes_missing_locally=0,
            records_fetched=0,
            records_persisted=0,
        )

        assert await gossip._auto_shadow_workflow(workflow_id, record)
        # Second call must also succeed (idempotent).
        assert await gossip._auto_shadow_workflow(workflow_id, record)
