"""End-to-end edge-case coverage for the s8 HITL surface.

These run against the FastAPI app via ``httpx.ASGITransport`` (same
pattern as ``test_approvals_api_federated.py``) and deliberately
bypass the scheduler / LLM. The scheduler itself is covered by
``test_scheduler_pause_sentinel.py`` and the OpenClaw harness; what
we exercise here is the *API surface every operator or federated peer
depends on*: duplicate approve, deny-then-resume, bad checkpoint hash,
stale Redis TTL, SSE route wiring, and the janitor's reap-expired
loop.

The test seeds synthetic Workflow rows + ApprovalRequest / checkpoint
records directly, which is faithful to the API contract (those are
the records a peer node would see via gossip) while keeping the tests
hermetic and Anthropic-credit-free.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.identity.signer import generate_keypair
from app.main import app
from app.models.base import WorkflowStatus
from app.models.workflow import Workflow
from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
from app.orchestration.dag import DAG, DAGNode
from app.safety.approval import ApprovalManager, ApprovalRequest
from app.safety.federated_approval import FederatedApprovalStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_session, redis_client, tmp_path, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    async def _db_override() -> AsyncIterator:
        yield db_session

    async def _redis_override() -> AsyncIterator:
        yield redis_client

    monkeypatch.setenv("IDENTITY_INSTANCE_KEY_PATH", str(tmp_path / "instance.key"))
    monkeypatch.setenv("IDENTITY_KEY_ENCRYPTION_KEY", "33" * 32)

    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    from app.services.identity_service import get_instance_key_store

    get_instance_key_store.cache_clear()  # type: ignore[attr-defined]

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_redis] = _redis_override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://massclaw.test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _seed_workflow(db_session: AsyncSession, workflow_id: uuid.UUID | None = None) -> uuid.UUID:
    workflow_id = workflow_id or uuid.uuid4()
    wf = Workflow(
        workflow_id=workflow_id,
        user_id="hitl-test",
        prompt="hitl edge cases",
        status=WorkflowStatus.RUNNING,
        budget_limit=100.0,
    )
    db_session.add(wf)
    await db_session.flush()
    return workflow_id


async def _create_pending_approval(
    db_session: AsyncSession,
    redis_client,
    workflow_id: uuid.UUID,
    node_id: str = "t1",
    checkpoint_hash: str | None = None,
    ttl_seconds: int = 600,
) -> tuple[ApprovalRequest, str]:
    """Create a signed checkpoint + approval request (Redis + CRDT) for the workflow.

    Returns the ApprovalRequest and the checkpoint hash.
    """
    keypair = generate_keypair()
    dag = DAG(
        [
            DAGNode(
                node_id=node_id,
                capability="compliance-check",
                description="synthetic HITL gate",
                depends_on=[],
            )
        ]
    )
    cp_store = CheckpointStore(session=db_session, keypair=keypair)
    saved = await cp_store.save(
        WorkflowCheckpoint.from_dag(
            workflow_id=workflow_id,
            dag=dag,
            reason=f"awaiting_approval: compliance-check [{node_id}]",
            current_task_id=node_id,
        )
    )
    actual_hash = saved.content_hash or ""

    # Write approval request to Redis (fast path).
    mgr = ApprovalManager(redis_client)
    now = datetime.now(UTC)
    request = ApprovalRequest(
        workflow_id=str(workflow_id),
        task_id=None,
        action="execute_compliance-check",
        context={"workflow_id": str(workflow_id), "capability": "compliance-check", "node_id": node_id},
        policy_rule="high_risk_task",
        status="pending",
        requested_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        checkpoint_hash=checkpoint_hash or actual_hash,
    )
    await redis_client.set(f"approval:{request.request_id}", request.model_dump_json(), ex=ttl_seconds)
    await redis_client.sadd("approval:pending", request.request_id)

    # Write CRDT twin too (mirrors what the scheduler does).
    federated = FederatedApprovalStore(session=db_session, keypair=keypair)
    await federated.write_request(request, node_id=node_id)

    return request, actual_hash


# ---------------------------------------------------------------------------
# Approve endpoint
# ---------------------------------------------------------------------------


class TestApprove:
    @pytest.mark.asyncio
    async def test_approve_happy_path(self, client: httpx.AsyncClient, db_session, redis_client):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)

        resp = await client.post(
            f"/api/v1/approvals/{req.request_id}/approve",
            json={"decided_by": "tester"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "approved", "request_id": req.request_id}

    @pytest.mark.asyncio
    async def test_duplicate_approve_returns_409(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)

        ok = await client.post(f"/api/v1/approvals/{req.request_id}/approve", json={"decided_by": "a"})
        assert ok.status_code == 200

        second = await client.post(f"/api/v1/approvals/{req.request_id}/approve", json={"decided_by": "b"})
        assert second.status_code == 409, second.text
        body = second.json()
        # Error envelope shape varies (detail / error_code) — just assert the keyword.
        assert "already" in second.text

    @pytest.mark.asyncio
    async def test_approve_unknown_id_returns_404(self, client: httpx.AsyncClient):
        resp = await client.post(
            f"/api/v1/approvals/{uuid.uuid4()}/approve",
            json={"decided_by": "ghost"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_after_deny_returns_409(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)

        denied = await client.post(
            f"/api/v1/approvals/{req.request_id}/deny",
            json={"decided_by": "a", "reason": "nope"},
        )
        assert denied.status_code == 200

        second = await client.post(
            f"/api/v1/approvals/{req.request_id}/approve",
            json={"decided_by": "b"},
        )
        assert second.status_code == 409

    @pytest.mark.asyncio
    async def test_approve_with_stale_redis_falls_back_to_crdt(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        """Simulates the peer-owned or TTL-expired case: CRDT has the pending
        request, Redis has forgotten it. Approve must still succeed via the
        federated fallback path."""
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)

        # Nuke Redis to simulate TTL eviction.
        await redis_client.delete(f"approval:{req.request_id}")
        await redis_client.srem("approval:pending", req.request_id)

        resp = await client.post(
            f"/api/v1/approvals/{req.request_id}/approve",
            json={"decided_by": "peer-operator"},
        )
        assert resp.status_code == 200, resp.text

        # Decision should now be in CRDT too.
        from app.services.identity_service import get_instance_key_store

        keypair = get_instance_key_store().instance_keypair()
        federated = FederatedApprovalStore(session=db_session, keypair=keypair)
        latest = await federated.find_by_request_id(req.request_id)
        assert latest is not None
        assert latest.status == "approved"


# ---------------------------------------------------------------------------
# Deny endpoint
# ---------------------------------------------------------------------------


class TestDeny:
    @pytest.mark.asyncio
    async def test_deny_happy_path(self, client: httpx.AsyncClient, db_session, redis_client):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)
        resp = await client.post(
            f"/api/v1/approvals/{req.request_id}/deny",
            json={"decided_by": "tester", "reason": "policy"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_deny_twice_returns_409(self, client: httpx.AsyncClient, db_session, redis_client):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)
        assert (await client.post(f"/api/v1/approvals/{req.request_id}/deny", json={"decided_by": "a"})).status_code == 200
        second = await client.post(f"/api/v1/approvals/{req.request_id}/deny", json={"decided_by": "b"})
        assert second.status_code == 409

    @pytest.mark.asyncio
    async def test_deny_unknown_returns_404(self, client: httpx.AsyncClient):
        resp = await client.post(
            f"/api/v1/approvals/{uuid.uuid4()}/deny", json={"decided_by": "ghost"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Resume endpoint — the single re-entry point for HITL
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_requires_approved_status(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        """Resume with a pending approval_id must 409."""
        wf = await _seed_workflow(db_session)
        req, ch = await _create_pending_approval(db_session, redis_client, wf)

        resp = await client.post(
            f"/api/v1/workflows/{wf}/resume",
            json={"checkpoint_hash": ch, "approval_id": req.request_id},
        )
        assert resp.status_code == 409, resp.text
        assert "approved" in resp.text or "pending" in resp.text

    @pytest.mark.asyncio
    async def test_resume_after_deny_returns_409(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf = await _seed_workflow(db_session)
        req, ch = await _create_pending_approval(db_session, redis_client, wf)
        await client.post(f"/api/v1/approvals/{req.request_id}/deny", json={"decided_by": "a"})

        resp = await client.post(
            f"/api/v1/workflows/{wf}/resume",
            json={"checkpoint_hash": ch, "approval_id": req.request_id},
        )
        assert resp.status_code == 409, resp.text
        assert "denied" in resp.text.lower() or "not 'approved'" in resp.text.lower() or "not `approved`" in resp.text.lower() or "approved" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_resume_with_bad_checkpoint_hash_returns_4xx(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)
        await client.post(f"/api/v1/approvals/{req.request_id}/approve", json={"decided_by": "a"})

        resp = await client.post(
            f"/api/v1/workflows/{wf}/resume",
            json={
                "checkpoint_hash": "z6MkBOGUS-hash-that-does-not-exist",
                "approval_id": req.request_id,
            },
        )
        # Either 404 (checkpoint missing) or 409 (hash mismatch with approval).
        assert resp.status_code in (404, 409), resp.text

    @pytest.mark.asyncio
    async def test_resume_with_mismatched_approval_checkpoint_returns_409(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        """approval.checkpoint_hash != body.checkpoint_hash is a replay-protection
        guard that must return 409."""
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)
        await client.post(f"/api/v1/approvals/{req.request_id}/approve", json={"decided_by": "a"})

        # Create a SECOND workflow/checkpoint; use that hash with the first approval.
        wf2 = await _seed_workflow(db_session)
        _, ch2 = await _create_pending_approval(db_session, redis_client, wf2, node_id="t2")

        resp = await client.post(
            f"/api/v1/workflows/{wf}/resume",
            json={"checkpoint_hash": ch2, "approval_id": req.request_id},
        )
        assert resp.status_code == 409, resp.text

    @pytest.mark.asyncio
    async def test_resume_with_checkpoint_for_different_workflow_returns_409(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf_a = await _seed_workflow(db_session)
        wf_b = await _seed_workflow(db_session)
        _, ch_b = await _create_pending_approval(db_session, redis_client, wf_b)

        resp = await client.post(
            f"/api/v1/workflows/{wf_a}/resume",
            json={"checkpoint_hash": ch_b},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Approval timeout / janitor
# ---------------------------------------------------------------------------


class TestJanitor:
    @pytest.mark.asyncio
    async def test_janitor_reaps_expired_and_transitions_workflow(
        self, db_session, redis_client
    ):
        """The janitor must:
        1. Mark expired pending approvals in Redis + CRDT.
        2. Transition the owning workflow from AWAITING_APPROVAL → FAILED
           with ``result.error == "approval timeout"``.
        """
        from app.safety.approval_janitor import _reap_expired

        # Seed a workflow in AWAITING_APPROVAL state.
        wf_id = uuid.uuid4()
        wf = Workflow(
            workflow_id=wf_id,
            user_id="janitor-test",
            prompt="janitor timeout",
            status=WorkflowStatus.AWAITING_APPROVAL,
            budget_limit=10.0,
        )
        db_session.add(wf)
        await db_session.flush()

        # Create an already-past-expiry approval.
        keypair = generate_keypair()
        dag = DAG([DAGNode(node_id="t1", capability="compliance-check", description="x", depends_on=[])])
        cp_store = CheckpointStore(session=db_session, keypair=keypair)
        saved = await cp_store.save(
            WorkflowCheckpoint.from_dag(workflow_id=wf_id, dag=dag, reason="awaiting_approval: test")
        )
        # IMPORTANT: the janitor's _transition_workflow_to_failed opens its
        # own db_session_context — flush the seeded workflow to a real
        # commit so a separate connection can see the row.
        await db_session.commit()

        now = datetime.now(UTC)
        request = ApprovalRequest(
            workflow_id=str(wf_id),
            action="execute_compliance-check",
            policy_rule="high_risk_task",
            status="pending",
            requested_at=(now - timedelta(seconds=400)).isoformat(),
            expires_at=(now - timedelta(seconds=10)).isoformat(),  # already expired
            checkpoint_hash=saved.content_hash,
        )
        await redis_client.set(f"approval:{request.request_id}", request.model_dump_json(), ex=60)
        await redis_client.sadd("approval:pending", request.request_id)

        mgr = ApprovalManager(redis_client)
        expired_count, transitioned = await _reap_expired(mgr)

        assert expired_count >= 1
        assert transitioned >= 1

        # Redis entry is now marked expired.
        raw = await redis_client.get(f"approval:{request.request_id}")
        assert raw is not None
        reloaded = ApprovalRequest.model_validate_json(raw)
        assert reloaded.status == "expired"

        # Workflow row is FAILED with the timeout error.
        # Re-fetch from a fresh session so we see the janitor's autocommit.
        from app.core.database import db_session_context

        async with db_session_context() as s:
            result = await s.execute(select(Workflow).where(Workflow.workflow_id == wf_id))
            wf_after = result.scalar_one()
            assert wf_after.status == WorkflowStatus.FAILED
            assert wf_after.result is not None
            assert wf_after.result.get("error") == "approval timeout"

    @pytest.mark.asyncio
    async def test_janitor_no_op_when_nothing_expired(self, redis_client):
        """With no pending approvals, the janitor returns (0, 0)."""
        from app.safety.approval_janitor import _reap_expired

        # Start from a clean pending set.
        await redis_client.delete("approval:pending")
        mgr = ApprovalManager(redis_client)
        assert await _reap_expired(mgr) == (0, 0)

    @pytest.mark.asyncio
    async def test_janitor_skips_not_yet_expired(self, db_session, redis_client):
        """A request whose ``expires_at`` is still in the future is skipped."""
        from app.safety.approval_janitor import _reap_expired

        wf = await _seed_workflow(db_session)
        await _create_pending_approval(db_session, redis_client, wf, ttl_seconds=600)

        mgr = ApprovalManager(redis_client)
        # It's possible other leftover test records have expired — assert
        # the specific request we seeded is still pending.
        expired, _ = await _reap_expired(mgr)
        # Either 0 (clean suite) or some unrelated expired entries; our
        # fresh one must survive.
        pending_after = await mgr.get_pending(workflow_id=str(wf))
        assert len(pending_after) == 1


# ---------------------------------------------------------------------------
# SSE stream route
# ---------------------------------------------------------------------------


class TestSSE:
    def test_stream_route_registered(self) -> None:
        """Sanity check the SSE route is mounted; live streaming is covered
        by the out-of-process smoke test."""
        paths = {getattr(route, "path", "") for route in app.router.routes}
        assert "/api/v1/approvals/stream" in paths


# ---------------------------------------------------------------------------
# Cross-node state reconciliation: Redis pending + CRDT decided
# ---------------------------------------------------------------------------


class TestFederatedStateReconciliation:
    """Redis is per-node; CRDT gossips across the whole federation. When the
    two disagree (Redis still thinks 'pending' but a peer has decided via
    gossip), the CRDT decision must win."""

    @pytest.mark.asyncio
    async def test_get_approval_prefers_crdt_decision_over_stale_redis_pending(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)

        # Simulate: a peer approved it via gossip — CRDT has approved,
        # local Redis still shows pending.
        from app.services.identity_service import get_instance_key_store

        keypair = get_instance_key_store().instance_keypair()
        federated = FederatedApprovalStore(session=db_session, keypair=keypair)
        req.status = "approved"
        from datetime import UTC, datetime

        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "peer-node-b"
        await federated.write_decision(req)

        resp = await client.get(f"/api/v1/approvals/{req.request_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved", body
        assert body["decided_by"] == "peer-node-b"

    @pytest.mark.asyncio
    async def test_pending_list_suppresses_decisions_seen_via_gossip(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        wf = await _seed_workflow(db_session)
        req, _ = await _create_pending_approval(db_session, redis_client, wf)

        from app.services.identity_service import get_instance_key_store

        keypair = get_instance_key_store().instance_keypair()
        federated = FederatedApprovalStore(session=db_session, keypair=keypair)
        req.status = "denied"
        from datetime import UTC, datetime

        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "peer-node-b"
        await federated.write_decision(req)

        # /approvals/pending must not surface this request even though
        # the local Redis entry still says 'pending'.
        resp = await client.get("/api/v1/approvals/pending")
        assert resp.status_code == 200
        ids = {r["request_id"] for r in resp.json()}
        assert req.request_id not in ids

    @pytest.mark.asyncio
    async def test_resume_accepts_approval_decided_only_in_crdt(
        self, client: httpx.AsyncClient, db_session, redis_client
    ):
        """Cross-node scenario: request created locally, decided on a
        peer. Local Redis is stale; CRDT has the approval. Resume
        must succeed against the CRDT state."""
        wf = await _seed_workflow(db_session)
        req, ch = await _create_pending_approval(db_session, redis_client, wf)

        from app.services.identity_service import get_instance_key_store

        keypair = get_instance_key_store().instance_keypair()
        federated = FederatedApprovalStore(session=db_session, keypair=keypair)
        req.status = "approved"
        from datetime import UTC, datetime

        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "peer-node-b"
        await federated.write_decision(req)

        resp = await client.post(
            f"/api/v1/workflows/{wf}/resume",
            json={"checkpoint_hash": ch, "approval_id": req.request_id},
        )
        # 200 or 202 means the resume flow accepted the approval.
        assert resp.status_code in (200, 202), resp.text
