"""Integration tests for :mod:`app.safety.audit`.

Writes signed policy-decision records via the real CRDT store, then
verifies them back via ``list_decisions`` and the
``GET /audit/policy-decisions`` endpoint.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.identity.signer import generate_keypair
from app.main import app
from app.models.base import WorkflowStatus
from app.models.workflow import Workflow
from app.safety.audit import is_policy_decision_record, list_decisions, record_decision
from app.safety.context import PolicyContext
from app.safety.decision import Decision, DecisionAction


@pytest_asyncio.fixture
async def workflow_row(db_session):
    wf = Workflow(
        user_id="demo",
        prompt="decision audit",
        budget_limit=100.0,
        status=WorkflowStatus.RUNNING,
    )
    db_session.add(wf)
    await db_session.flush()
    return wf


@pytest_asyncio.fixture
async def keypair():
    return generate_keypair()


@pytest_asyncio.fixture
async def client(db_session, redis_client) -> AsyncIterator[AsyncClient]:
    from app.core.database import get_db_session
    from app.core.redis import get_redis

    async def _db_override() -> AsyncIterator:
        yield db_session

    async def _redis_override() -> AsyncIterator:
        yield redis_client

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_redis] = _redis_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _ctx(wf_id: uuid.UUID, **overrides) -> PolicyContext:
    defaults = dict(
        agent_did="did:key:z6MkAudit",
        agent_trust_score=0.8,
        action="execute_tool",
        tool_name="web_search",
        workflow_id=wf_id,
        task_id=uuid.uuid4(),
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


class TestRecordDecision:
    @pytest.mark.asyncio
    async def test_deny_is_persisted(self, db_session, keypair, workflow_row):
        decision = Decision.deny(rule_id="tst_deny", reason="blocked in test")
        rec = await record_decision(
            session=db_session,
            keypair=keypair,
            decision=decision,
            ctx=_ctx(workflow_row.workflow_id),
        )
        assert rec is not None
        assert is_policy_decision_record(rec) is True
        assert rec.content.startswith("policy.deny")
        # Decision payload survives the round trip.
        from app.safety.audit import POLICY_CONTEXT_SUMMARY_KEY, POLICY_DECISION_PAYLOAD_KEY

        payload = rec.metadata_[POLICY_DECISION_PAYLOAD_KEY]
        assert payload["action"] == "deny"
        assert payload["rule_id"] == "tst_deny"
        # Context summary drops handles.
        summary = rec.metadata_[POLICY_CONTEXT_SUMMARY_KEY]
        assert "session" not in summary
        assert "redis" not in summary
        assert summary["agent_did"] == "did:key:z6MkAudit"

    @pytest.mark.asyncio
    async def test_abstain_is_not_persisted(self, db_session, keypair, workflow_row):
        decision = Decision.abstain(rule_id="tst_abstain", reason="no opinion")
        rec = await record_decision(
            session=db_session,
            keypair=keypair,
            decision=decision,
            ctx=_ctx(workflow_row.workflow_id),
        )
        assert rec is None

    @pytest.mark.asyncio
    async def test_missing_workflow_id_is_skipped(self, db_session, keypair):
        decision = Decision.deny(rule_id="tst", reason="x")
        rec = await record_decision(
            session=db_session,
            keypair=keypair,
            decision=decision,
            ctx=_ctx(None, workflow_id=None),
        )
        assert rec is None


class TestListDecisions:
    @pytest.mark.asyncio
    async def test_returns_newest_first_filtered_by_action(self, db_session, keypair, workflow_row):
        # Scope assertions to this workflow_id so parallel tests writing
        # to the same DB don't bleed into our counts.
        await record_decision(
            session=db_session,
            keypair=keypair,
            decision=Decision.allow(rule_id="a1", reason="ok"),
            ctx=_ctx(workflow_row.workflow_id, action="x1"),
        )
        await record_decision(
            session=db_session,
            keypair=keypair,
            decision=Decision.deny(rule_id="d1", reason="no"),
            ctx=_ctx(workflow_row.workflow_id, action="x2"),
        )
        await record_decision(
            session=db_session,
            keypair=keypair,
            decision=Decision.allow(rule_id="a2", reason="ok2"),
            ctx=_ctx(workflow_row.workflow_id, action="x3"),
        )

        all_ = await list_decisions(db_session, workflow_id=workflow_row.workflow_id)
        assert len(all_) == 3

        denies = await list_decisions(db_session, workflow_id=workflow_row.workflow_id, action=DecisionAction.DENY)
        assert len(denies) == 1
        assert (denies[0].metadata_ or {})["decision"]["rule_id"] == "d1"

    @pytest.mark.asyncio
    async def test_filter_by_workflow(self, db_session, keypair, workflow_row):
        other_wf = Workflow(
            user_id="demo",
            prompt="unrelated",
            budget_limit=10.0,
            status=WorkflowStatus.RUNNING,
        )
        db_session.add(other_wf)
        await db_session.flush()

        await record_decision(
            session=db_session,
            keypair=keypair,
            decision=Decision.allow(rule_id="a1"),
            ctx=_ctx(workflow_row.workflow_id),
        )
        await record_decision(
            session=db_session,
            keypair=keypair,
            decision=Decision.allow(rule_id="a2"),
            ctx=_ctx(other_wf.workflow_id),
        )

        mine = await list_decisions(db_session, workflow_id=workflow_row.workflow_id)
        assert len(mine) == 1


class TestAuditEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_persisted_decisions(self, client, db_session, keypair, workflow_row):
        await record_decision(
            session=db_session,
            keypair=keypair,
            decision=Decision.escalate_human(rule_id="ep1", reason="please review"),
            ctx=_ctx(workflow_row.workflow_id),
        )
        await db_session.commit()

        resp = await client.get("/api/v1/audit/policy-decisions")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1
        first = next(b for b in body if b["decision"]["rule_id"] == "ep1")
        assert first["decision"]["action"] == "escalate_human"
        assert first["workflow_id"] == str(workflow_row.workflow_id)

    @pytest.mark.asyncio
    async def test_bad_action_filter_is_422(self, client):
        resp = await client.get("/api/v1/audit/policy-decisions?action=shrug")
        assert resp.status_code == 422
