"""Integration tests for the approval timeout janitor.

``TestJanitor`` in test_hitl_edge_cases.py already covered the happy path: an
expired request sitting in Redis gets reaped and its workflow transitioned.
What it could not catch is that the janitor rarely *reached* that path in
practice, because the Redis key expired at the same instant the request became
reapable. This file covers the surrounding cases — the TTL window, approvals
that live only in the CRDT store, and pending-set entries whose payload is
already gone.

That matters because the janitor is the only thing standing between a workflow
parked in ``AWAITING_APPROVAL`` and a terminal state. When it silently skips
one, nothing else retries it.

Note these tests **commit** their seed rows. The janitor deliberately opens
its own session via ``db_session_context()`` (it is a background task, not a
request handler), so it cannot see uncommitted work from the ``db_session``
fixture. Cleanup is by ``_test``-database isolation — see tests/conftest.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.database import db_session_context
from app.identity.signer import generate_keypair
from app.models.base import MemoryType, WorkflowStatus
from app.models.memory import MemoryRecord
from app.models.workflow import Workflow
from app.safety.approval import _PENDING_SET, ApprovalManager, ApprovalRequest
from app.safety.approval_janitor import _reap_expired
from app.safety.federated_approval import FederatedApprovalStore


@pytest_asyncio.fixture
async def keypair():
    return generate_keypair()


@pytest_asyncio.fixture
async def initialised_db(db_session):
    """Guarantee the engine and tables exist for sessions we do not own.

    The janitor opens its own ``db_session_context()``, so these tests never
    touch ``db_session`` directly — they just need the setup it performs.
    """
    yield


@pytest_asyncio.fixture(autouse=True)
async def _clean_approval_state(redis_client, initialised_db):
    """Give each test an empty approval world.

    These tests have to **commit** their fixtures for the janitor's own session
    to see them, so rows survive the test that created them. Since a tick now
    scans the whole CRDT store, one test's leftovers would be reaped by the
    next one's tick and throw off its counts — and a stale expired record would
    break the "nothing was touched" assertions outright.

    Only approval state is cleared, and only in the isolated ``_test``
    database. Workflow rows are left alone: they are looked up by id, so they
    are inert once no approval record points at them.
    """
    async with db_session_context() as session:
        await session.execute(delete(MemoryRecord).where(MemoryRecord.memory_type == MemoryType.META))
        await session.commit()
    for key in await redis_client.keys("approval:*"):
        await redis_client.delete(key)
    yield


async def _seed_committed_workflow(
    workflow_id: uuid.UUID,
    status: WorkflowStatus = WorkflowStatus.AWAITING_APPROVAL,
) -> None:
    """Commit a workflow row the janitor's own session can see."""
    async with db_session_context() as session:
        session.add(
            Workflow(
                workflow_id=workflow_id,
                user_id="test",
                prompt="approval janitor test",
                status=status,
                budget_limit=10.0,
            )
        )
        await session.commit()


async def _workflow_status(workflow_id: uuid.UUID) -> WorkflowStatus:
    async with db_session_context() as session:
        result = await session.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))
        wf = result.scalar_one()
        return wf.status


async def _workflow_result(workflow_id: uuid.UUID) -> dict | None:
    async with db_session_context() as session:
        result = await session.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))
        return result.scalar_one().result


def _request(workflow_id: str, *, expires_in_seconds: int, **overrides) -> ApprovalRequest:
    now = datetime.now(UTC)
    base = {
        "workflow_id": workflow_id,
        "action": "execute_compliance-check",
        "policy_rule": "high_risk_task",
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_in_seconds)).isoformat(),
    }
    base.update(overrides)
    return ApprovalRequest(**base)


async def _put_in_redis(redis_client, request: ApprovalRequest, *, ttl: int = 600) -> None:
    """Write a request to Redis directly, bypassing request_approval.

    Lets a test place an already-expired record without sleeping.
    """
    mgr = ApprovalManager(redis_client)
    await redis_client.set(mgr._key(request.request_id), request.model_dump_json(), ex=ttl)
    await redis_client.sadd(_PENDING_SET, request.request_id)


class TestRedisKeyOutlivesLogicalExpiry:
    """The root cause of the janitor missing most expirations.

    ``request_approval`` used to set the Redis TTL to exactly
    ``timeout_seconds`` — the same value used for ``expires_at`` — so the key
    vanished at the very moment it became reapable. With a 30s tick, the
    janitor lost the race routinely, and on the losing path it took the
    ``raw is None`` branch, which dropped the id and never transitioned the
    workflow.
    """

    @pytest.mark.asyncio
    async def test_ttl_extends_past_expires_at(self, redis_client, initialised_db):
        mgr = ApprovalManager(redis_client)
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)

        request = await mgr.request_approval(
            workflow_id=str(wf),
            task_id="task-1",
            action="execute_compliance-check",
            context={},
            policy_rule="high_risk_task",
            timeout_seconds=300,
        )

        ttl = await redis_client.ttl(mgr._key(request.request_id))
        assert ttl > 300, (
            f"Redis TTL ({ttl}s) must outlive the 300s logical expiry so the "
            f"janitor can observe the expired record and reap it properly."
        )


class TestReapingExpiredRequests:
    @pytest.mark.asyncio
    async def test_expired_request_is_marked_and_workflow_failed(self, redis_client, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(str(wf), expires_in_seconds=-60)
        await _put_in_redis(redis_client, request)

        expired, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert (expired, transitioned) == (1, 1)
        stored = await ApprovalManager(redis_client).check_status(request.request_id)
        assert stored.status == "expired"
        assert stored.decided_at is not None
        assert not await redis_client.sismember(_PENDING_SET, request.request_id)
        assert await _workflow_status(wf) == WorkflowStatus.FAILED
        assert (await _workflow_result(wf) or {}).get("error") == "approval timeout"

    @pytest.mark.asyncio
    async def test_unexpired_request_is_left_alone(self, redis_client, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(str(wf), expires_in_seconds=600)
        await _put_in_redis(redis_client, request)

        expired, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert (expired, transitioned) == (0, 0)
        assert await redis_client.sismember(_PENDING_SET, request.request_id)
        assert await _workflow_status(wf) == WorkflowStatus.AWAITING_APPROVAL

    @pytest.mark.asyncio
    async def test_already_decided_request_is_skipped(self, redis_client, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(
            str(wf),
            expires_in_seconds=-60,
            status="approved",
            decided_by="human",
            decided_at=datetime.now(UTC).isoformat(),
        )
        await _put_in_redis(redis_client, request)

        expired, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert (expired, transitioned) == (0, 0)
        # An approved-then-resumed workflow must not be dragged to FAILED.
        assert await _workflow_status(wf) == WorkflowStatus.AWAITING_APPROVAL

    @pytest.mark.asyncio
    async def test_malformed_expires_at_is_treated_as_expired(self, redis_client, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(str(wf), expires_in_seconds=60, expires_at="not-a-timestamp")
        await _put_in_redis(redis_client, request)

        expired, _ = await _reap_expired(ApprovalManager(redis_client))

        assert expired == 1
        assert await _workflow_status(wf) == WorkflowStatus.FAILED


class TestOrphanedPendingSetMembers:
    """Members of ``approval:pending`` whose payload key is gone.

    Five of these were sitting in the dev Redis when this was written, none
    with a surviving ``approval:{id}`` key. The old code dropped the id and
    moved on, leaving the workflow parked in AWAITING_APPROVAL with no
    terminal signal and nothing left to retry it.
    """

    @pytest.mark.asyncio
    async def test_orphan_with_crdt_twin_is_reaped_via_crdt(self, redis_client, keypair, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(str(wf), expires_in_seconds=-60)

        # Twin exists in the CRDT store, but the Redis payload is gone.
        async with db_session_context() as session:
            store = FederatedApprovalStore(session=session, keypair=keypair)
            await store.write_request(request, node_id="task-1")
            await session.commit()
        await redis_client.sadd(_PENDING_SET, request.request_id)

        _, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert transitioned == 1, "workflow_id was recoverable from the CRDT twin"
        assert await _workflow_status(wf) == WorkflowStatus.FAILED
        assert not await redis_client.sismember(_PENDING_SET, request.request_id)

        async with db_session_context() as session:
            store = FederatedApprovalStore(session=session, keypair=keypair)
            latest = await store.find_by_request_id(request.request_id)
        assert latest is not None
        assert latest.status == "expired"

    @pytest.mark.asyncio
    async def test_orphan_without_twin_is_dropped_quietly(self, redis_client, initialised_db):
        """Nothing to recover the workflow_id from, so just clean the set."""
        orphan_id = str(uuid.uuid4())
        await redis_client.sadd(_PENDING_SET, orphan_id)

        expired, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert (expired, transitioned) == (0, 0)
        assert not await redis_client.sismember(_PENDING_SET, orphan_id)


class TestCrdtOnlyPendingApprovals:
    """Approvals that never existed in this node's Redis.

    Gossiped in from a peer, or local ones whose Redis key aged out entirely.
    The janitor only ever read the Redis pending set, so these were invisible
    to it and stayed 'pending' indefinitely — which is what put a four-month-old
    request on the live /approvals/pending response.
    """

    @pytest.mark.asyncio
    async def test_expired_crdt_only_pending_is_reaped(self, redis_client, keypair, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(str(wf), expires_in_seconds=-60)

        async with db_session_context() as session:
            store = FederatedApprovalStore(session=session, keypair=keypair)
            await store.write_request(request, node_id="task-1")
            await session.commit()

        _, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert transitioned == 1
        assert await _workflow_status(wf) == WorkflowStatus.FAILED

    @pytest.mark.asyncio
    async def test_unexpired_crdt_only_pending_is_left_alone(self, redis_client, keypair, initialised_db):
        wf = uuid.uuid4()
        await _seed_committed_workflow(wf)
        request = _request(str(wf), expires_in_seconds=600)

        async with db_session_context() as session:
            store = FederatedApprovalStore(session=session, keypair=keypair)
            await store.write_request(request, node_id="task-1")
            await session.commit()

        expired, transitioned = await _reap_expired(ApprovalManager(redis_client))

        assert (expired, transitioned) == (0, 0)
        assert await _workflow_status(wf) == WorkflowStatus.AWAITING_APPROVAL


class TestFederatedStoreExpiryFiltering:
    """``get_pending`` must not advertise expired requests as pending.

    The janitor reaping them is the authoritative fix, but the read path
    should not display a request as actionable in the window before a tick
    lands. ``ApprovalManager.get_pending`` already wall-clock filters; this
    brings the CRDT side in line.
    """

    @pytest.mark.asyncio
    async def test_expired_pending_is_not_returned(self, db_session, keypair):
        wf = uuid.uuid4()
        db_session.add(
            Workflow(
                workflow_id=wf,
                user_id="test",
                prompt="expiry filter test",
                status=WorkflowStatus.AWAITING_APPROVAL,
                budget_limit=10.0,
            )
        )
        await db_session.flush()

        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        await store.write_request(_request(str(wf), expires_in_seconds=-60), node_id="task-1")

        assert await store.get_pending(workflow_id=str(wf)) == []

    @pytest.mark.asyncio
    async def test_unexpired_pending_is_still_returned(self, db_session, keypair):
        wf = uuid.uuid4()
        db_session.add(
            Workflow(
                workflow_id=wf,
                user_id="test",
                prompt="expiry filter test",
                status=WorkflowStatus.AWAITING_APPROVAL,
                budget_limit=10.0,
            )
        )
        await db_session.flush()

        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        request = _request(str(wf), expires_in_seconds=600)
        await store.write_request(request, node_id="task-1")

        pending = await store.get_pending(workflow_id=str(wf))
        assert [p.request_id for p in pending] == [request.request_id]

    @pytest.mark.asyncio
    async def test_expired_request_is_still_findable_by_id(self, db_session, keypair):
        """Filtering applies to the pending *list* only. Resume validation and
        /approvals/{id} still need to resolve an expired request."""
        wf = uuid.uuid4()
        db_session.add(
            Workflow(
                workflow_id=wf,
                user_id="test",
                prompt="expiry filter test",
                status=WorkflowStatus.AWAITING_APPROVAL,
                budget_limit=10.0,
            )
        )
        await db_session.flush()

        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        request = _request(str(wf), expires_in_seconds=-60)
        await store.write_request(request, node_id="task-1")

        found = await store.find_by_request_id(request.request_id)
        assert found is not None
        assert found.request_id == request.request_id
