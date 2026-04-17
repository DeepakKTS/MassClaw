"""Integration tests for :class:`FederatedApprovalStore`.

These exercise the CRDT write+read round-trip end-to-end. Uses the
``db_session`` fixture from :mod:`tests.conftest`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.identity.signer import generate_keypair
from app.models.base import WorkflowStatus
from app.models.workflow import Workflow
from app.safety.approval import ApprovalRequest
from app.safety.federated_approval import FederatedApprovalStore


@pytest_asyncio.fixture
async def keypair():
    return generate_keypair()


async def _seed_workflow(db_session, workflow_id: uuid.UUID) -> None:
    """Create a Workflow row so the FK on ``memory_records`` is satisfied."""
    wf = Workflow(
        workflow_id=workflow_id,
        user_id="test",
        prompt="federated approval test",
        status=WorkflowStatus.RUNNING,
        budget_limit=10.0,
    )
    db_session.add(wf)
    await db_session.flush()


def _request(workflow_id: str, **overrides) -> ApprovalRequest:
    base = {
        "workflow_id": workflow_id,
        "action": "execute_compliance-check",
        "policy_rule": "high_risk_task",
    }
    base.update(overrides)
    return ApprovalRequest(**base)


class TestFederatedApprovalRoundTrip:
    @pytest.mark.asyncio
    async def test_write_request_is_readable_as_pending(self, db_session, keypair):
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        wf_uuid = uuid.uuid4()
        await _seed_workflow(db_session, wf_uuid)
        req = _request(workflow_id=str(wf_uuid))
        content_hash = await store.write_request(req, node_id="task-2")
        assert content_hash

        pending = await store.get_pending(workflow_id=str(wf_uuid))
        assert len(pending) == 1
        assert pending[0].request_id == req.request_id
        assert pending[0].status == "pending"

    @pytest.mark.asyncio
    async def test_write_decision_removes_pending(self, db_session, keypair):
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        wf_uuid = uuid.uuid4()
        await _seed_workflow(db_session, wf_uuid)
        req = _request(workflow_id=str(wf_uuid))
        await store.write_request(req, node_id="task-2")

        req.status = "approved"
        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "human-reviewer"
        await store.write_decision(req, node_id="task-2")

        pending = await store.get_pending(workflow_id=str(wf_uuid))
        assert pending == []

        latest = await store.find_by_request_id(req.request_id)
        assert latest is not None
        assert latest.status == "approved"
        assert latest.decided_by == "human-reviewer"

    @pytest.mark.asyncio
    async def test_find_decision_for_node_returns_approved(self, db_session, keypair):
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        workflow_uuid = uuid.uuid4()
        await _seed_workflow(db_session, workflow_uuid)
        req = _request(workflow_id=str(workflow_uuid))
        await store.write_request(req, node_id="task-3")

        decision = await store.find_decision_for_node(workflow_uuid, "task-3")
        assert decision is None  # still pending

        req.status = "approved"
        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "reviewer"
        await store.write_decision(req, node_id="task-3")

        decision = await store.find_decision_for_node(workflow_uuid, "task-3")
        assert decision is not None
        assert decision.status == "approved"
        assert decision.request_id == req.request_id

    @pytest.mark.asyncio
    async def test_denied_decision_is_surfaced(self, db_session, keypair):
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        wf_uuid = uuid.uuid4()
        await _seed_workflow(db_session, wf_uuid)
        req = _request(workflow_id=str(wf_uuid))
        await store.write_request(req, node_id="task-2")

        req.status = "denied"
        req.decided_at = datetime.now(UTC).isoformat()
        req.decided_by = "reviewer"
        req.context["denial_reason"] = "policy violation"
        await store.write_decision(req, node_id="task-2")

        pending = await store.get_pending(workflow_id=str(wf_uuid))
        assert pending == []
        latest = await store.find_by_request_id(req.request_id)
        assert latest is not None
        assert latest.status == "denied"
        assert latest.context.get("denial_reason") == "policy violation"

    @pytest.mark.asyncio
    async def test_write_decision_rejects_pending_request(self, db_session, keypair):
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        req = _request(workflow_id=str(uuid.uuid4()))
        with pytest.raises(ValueError, match="pending"):
            await store.write_decision(req)

    @pytest.mark.asyncio
    async def test_pending_from_different_workflow_is_filtered(self, db_session, keypair):
        store = FederatedApprovalStore(session=db_session, keypair=keypair)
        wf_a = uuid.uuid4()
        wf_b = uuid.uuid4()
        await _seed_workflow(db_session, wf_a)
        await _seed_workflow(db_session, wf_b)
        await store.write_request(_request(workflow_id=str(wf_a)), node_id="task-1")
        await store.write_request(_request(workflow_id=str(wf_b)), node_id="task-1")

        only_a = await store.get_pending(workflow_id=str(wf_a))
        assert len(only_a) == 1
        assert only_a[0].workflow_id == str(wf_a)

        all_pending = await store.get_pending()
        assert {r.workflow_id for r in all_pending} == {str(wf_a), str(wf_b)}
