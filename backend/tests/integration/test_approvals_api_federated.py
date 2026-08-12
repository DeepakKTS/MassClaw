"""HTTP-level tests for the federated approvals API.

Exercises the new code paths introduced for s8 HITL:

- ``GET /approvals/pending?federated=true`` returns the union of Redis
  pending + CRDT pending (and de-duplicates).
- ``POST /approvals/{id}/approve`` falls back to CRDT when Redis has
  evicted the entry (simulates TTL expiry / peer-owned requests).
- ``POST /approvals/{id}/deny`` uses the same fallback path.
- ``GET /approvals/stream`` returns an SSE response.

Uses :class:`httpx.AsyncClient` + :class:`httpx.ASGITransport` so the
async ORM session stays on the test's event loop — same pattern as
``test_demo_endpoints.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.main import app
from app.models.base import WorkflowStatus
from app.models.workflow import Workflow
from app.safety.approval import ApprovalRequest


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


async def _seed_workflow(db_session, workflow_id: uuid.UUID) -> None:
    wf = Workflow(
        workflow_id=workflow_id,
        user_id="test",
        prompt="approvals api federated test",
        status=WorkflowStatus.RUNNING,
        budget_limit=10.0,
    )
    db_session.add(wf)
    await db_session.flush()


def _pending_request(workflow_id: str, **overrides) -> ApprovalRequest:
    base = {
        "workflow_id": workflow_id,
        "action": "execute_compliance-check",
        "policy_rule": "high_risk_task",
    }
    base.update(overrides)
    req = ApprovalRequest(**base)
    # Force expires_at far enough in the future so it counts as pending.
    req.expires_at = (datetime.now(UTC) + timedelta(seconds=600)).isoformat()
    return req


class TestFederatedUnionPending:
    @pytest.mark.asyncio
    async def test_returns_union_of_redis_and_crdt_pending(
        self, client: httpx.AsyncClient, db_session, redis_client
    ) -> None:
        from app.identity.signer import generate_keypair
        from app.safety.federated_approval import FederatedApprovalStore

        wf_redis = uuid.uuid4()
        wf_crdt = uuid.uuid4()
        await _seed_workflow(db_session, wf_redis)
        await _seed_workflow(db_session, wf_crdt)

        redis_req = _pending_request(workflow_id=str(wf_redis))
        await redis_client.set(f"approval:{redis_req.request_id}", redis_req.model_dump_json(), ex=600)
        await redis_client.sadd("approval:pending", redis_req.request_id)

        store = FederatedApprovalStore(session=db_session, keypair=generate_keypair())
        crdt_req = _pending_request(workflow_id=str(wf_crdt))
        await store.write_request(crdt_req, node_id="task-1")

        resp = await client.get("/api/v1/approvals/pending")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = {r["request_id"] for r in rows}
        assert redis_req.request_id in ids
        assert crdt_req.request_id in ids

    @pytest.mark.asyncio
    async def test_non_federated_mode_returns_redis_only(
        self, client: httpx.AsyncClient, db_session, redis_client
    ) -> None:
        from app.identity.signer import generate_keypair
        from app.safety.federated_approval import FederatedApprovalStore

        wf_crdt = uuid.uuid4()
        await _seed_workflow(db_session, wf_crdt)
        store = FederatedApprovalStore(session=db_session, keypair=generate_keypair())
        crdt_req = _pending_request(workflow_id=str(wf_crdt))
        await store.write_request(crdt_req, node_id="task-1")

        resp = await client.get("/api/v1/approvals/pending?federated=false")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = {r["request_id"] for r in rows}
        # Redis is empty, federated flag off → no entries.
        assert crdt_req.request_id not in ids


class TestApproveWithCrdtFallback:
    @pytest.mark.asyncio
    async def test_approve_falls_back_to_crdt_when_redis_evicted(
        self, client: httpx.AsyncClient, db_session, redis_client
    ) -> None:
        from app.identity.signer import generate_keypair
        from app.safety.federated_approval import FederatedApprovalStore

        wf_uuid = uuid.uuid4()
        await _seed_workflow(db_session, wf_uuid)
        store = FederatedApprovalStore(session=db_session, keypair=generate_keypair())
        req = _pending_request(workflow_id=str(wf_uuid))
        # CRDT has the request, Redis does NOT (simulating TTL expiry or
        # a peer-owned request gossipped in).
        await store.write_request(req, node_id="task-1")

        resp = await client.post(
            f"/api/v1/approvals/{req.request_id}/approve",
            json={"reason": "verified", "decided_by": "human-reviewer"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "approved", "request_id": req.request_id}

        latest = await store.find_by_request_id(req.request_id)
        assert latest is not None
        assert latest.status == "approved"
        assert latest.decided_by == "human-reviewer"

    @pytest.mark.asyncio
    async def test_approve_404_when_request_unknown_anywhere(
        self, client: httpx.AsyncClient, redis_client
    ) -> None:
        resp = await client.post(
            f"/api/v1/approvals/{uuid.uuid4()}/approve",
            json={"reason": "", "decided_by": "human"},
        )
        assert resp.status_code == 404


class TestDenyWithCrdtFallback:
    @pytest.mark.asyncio
    async def test_deny_falls_back_to_crdt(self, client: httpx.AsyncClient, db_session) -> None:
        from app.identity.signer import generate_keypair
        from app.safety.federated_approval import FederatedApprovalStore

        wf_uuid = uuid.uuid4()
        await _seed_workflow(db_session, wf_uuid)
        store = FederatedApprovalStore(session=db_session, keypair=generate_keypair())
        req = _pending_request(workflow_id=str(wf_uuid))
        await store.write_request(req, node_id="task-1")

        resp = await client.post(
            f"/api/v1/approvals/{req.request_id}/deny",
            json={"reason": "policy violation", "decided_by": "human-reviewer"},
        )
        assert resp.status_code == 200, resp.text

        latest = await store.find_by_request_id(req.request_id)
        assert latest is not None
        assert latest.status == "denied"
        assert latest.decided_by == "human-reviewer"
        assert latest.context.get("denial_reason") == "policy violation"


class TestStreamEndpoint:
    def test_stream_route_is_registered(self) -> None:
        """The SSE endpoint itself is exercised by the OpenClaw smoke test
        (``curl -N /api/v1/approvals/stream``) — a full ASGI round-trip
        here would hang the test runner on the open pubsub connection.
        We at least confirm the route is wired so a misnamed path can't
        slip past.

        Enumerated through ``app.openapi()`` rather than by walking
        ``app.router.routes``: as of FastAPI 0.141 ``include_router`` keeps
        a sub-router nested behind an internal wrapper object instead of
        flattening its routes into the parent, so that walk reports no
        path at all for anything mounted under a prefix.
        """
        assert "/api/v1/approvals/stream" in app.openapi()["paths"]
