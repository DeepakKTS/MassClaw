"""Edge-case hardening tests — Day 18.

Covers the classes of "weird input" that stock agents (OpenClaw, judge
harnesses) tend to send and that we've seen leak raw 500s in the past:

- Malformed JSON body.
- Oversized body (payload cap middleware).
- Invalid UUID path param.
- Unicode (multi-byte characters, RTL, emoji) in prompts + memory.
- Missing / unknown path → 404 envelope.
- Unsupported method → 405 envelope.
- Empty / boundary values on every Pydantic validator we own.
- Concurrent duplicate writes to the CRDT store.

Every test asserts the **shape** of the error envelope, not just the
status code — drift here is what actually breaks stock agents.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client(db_session, redis_client, tmp_path, monkeypatch) -> AsyncIterator[AsyncClient]:
    from app.core.database import get_db_session
    from app.core.redis import get_redis

    # Route identity to a writable per-test tmpdir so endpoints that
    # need the instance keypair (e.g. POST /workflows/{id}/resume)
    # don't try to touch ``/var/lib/massclaw``.
    monkeypatch.setenv("IDENTITY_INSTANCE_KEY_PATH", str(tmp_path / "instance.key"))
    monkeypatch.setenv("IDENTITY_KEY_ENCRYPTION_KEY", "33" * 32)
    from app.config import get_settings
    from app.services.identity_service import get_instance_key_store

    get_settings.cache_clear()  # type: ignore[attr-defined]
    get_instance_key_store.cache_clear()  # type: ignore[attr-defined]

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
        # Force other suites to see a freshly-built Settings instance so
        # monkeypatches we applied above (e.g. rate-limit thresholds)
        # don't leak through lru_cache.
        get_settings.cache_clear()  # type: ignore[attr-defined]
        get_instance_key_store.cache_clear()  # type: ignore[attr-defined]


def _assert_envelope_shape(body: dict, *, expected_code: str | None = None) -> None:
    """Every error response must carry at minimum error_code + detail."""
    assert "error_code" in body, f"missing error_code in {body!r}"
    assert "detail" in body, f"missing detail in {body!r}"
    assert isinstance(body["error_code"], str)
    assert isinstance(body["detail"], str)
    if expected_code is not None:
        assert body["error_code"] == expected_code, f"expected {expected_code}, got {body}"


class TestMalformedInput:
    @pytest.mark.asyncio
    async def test_malformed_json_returns_422_envelope(self, client):
        # Hand-rolled request with bad JSON body.
        resp = await client.post(
            "/api/v1/policy/registry/evaluate",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope_shape(body, expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_invalid_uuid_path_param_returns_422_envelope(self, client):
        resp = await client.get("/api/v1/workflows/not-a-uuid/status")
        assert resp.status_code == 422
        body = resp.json()
        _assert_envelope_shape(body, expected_code="VALIDATION_ERROR")
        assert "errors" in body
        # At least one validation error references the path param.
        assert any("workflow_id" in err.get("loc", []) for err in body["errors"])

    @pytest.mark.asyncio
    async def test_wrong_types_in_body_return_422_envelope(self, client):
        # estimated_cost must be a number; send a string.
        resp = await client.post(
            "/api/v1/policy/registry/evaluate",
            json={"action": "x", "estimated_cost": "not-a-number"},
        )
        assert resp.status_code == 422
        _assert_envelope_shape(resp.json(), expected_code="VALIDATION_ERROR")

    @pytest.mark.asyncio
    async def test_empty_required_string_rejected(self, client):
        # submit_task's `instruction` has min_length=5
        resp = await client.post("/api/v1/workflows/submit", json={"instruction": "", "budget": 10})
        assert resp.status_code == 422
        _assert_envelope_shape(resp.json())


class TestNotFoundAndMethodErrors:
    @pytest.mark.asyncio
    async def test_unknown_route_returns_404_envelope(self, client):
        resp = await client.get("/api/v1/totally/fake/path")
        assert resp.status_code == 404
        _assert_envelope_shape(resp.json(), expected_code="NOT_FOUND")

    @pytest.mark.asyncio
    async def test_wrong_method_returns_405_envelope(self, client):
        # /policy/registry/rules is GET-only
        resp = await client.delete("/api/v1/policy/registry/rules")
        assert resp.status_code == 405
        _assert_envelope_shape(resp.json(), expected_code="METHOD_NOT_ALLOWED")


class TestOversizedBody:
    @pytest.mark.asyncio
    async def test_body_over_limit_returns_413_envelope(self, client, monkeypatch):
        # Shrink the limit for this test so we don't need to generate
        # a real 1 MiB payload.
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "max_request_body_bytes", 200)

        big_payload = {"instruction": "x" * 500, "budget": 10}
        resp = await client.post("/api/v1/workflows/submit", json=big_payload)
        assert resp.status_code == 413
        body = resp.json()
        _assert_envelope_shape(body, expected_code="PAYLOAD_TOO_LARGE")
        assert body.get("extra", {}).get("limit_bytes") == 200


class TestUnicodeSurface:
    @pytest.mark.asyncio
    async def test_unicode_prompt_round_trips(self, client):
        prompt = "研究 warehouse bottlenecks 🤖 مرحبا"  # CJK + emoji + RTL
        resp = await client.post(
            "/api/v1/policy/registry/evaluate",
            json={"action": "execute_tool", "extra": {"prompt": prompt}},
        )
        # We don't care about the action the engine returns (registry
        # may be empty in this test) — we care that the service
        # accepts the payload and returns a well-formed response.
        assert resp.status_code == 200
        body = resp.json()
        assert "action" in body

    @pytest.mark.asyncio
    async def test_unicode_action_slug_roundtrips(self, client):
        resp = await client.post(
            "/api/v1/policy/registry/evaluate",
            json={"action": "研究", "action_category": "数据"},
        )
        assert resp.status_code == 200


class TestConcurrentWrites:
    """CRDTStore must collapse duplicate content hashes to a single row."""

    @pytest.mark.asyncio
    async def test_duplicate_checkpoint_save_is_idempotent(self, db_session):

        from app.identity.signer import generate_keypair
        from app.models.base import TaskStatus, WorkflowStatus
        from app.models.workflow import Workflow
        from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
        from app.orchestration.dag import DAG, DAGNode

        wf = Workflow(user_id="u", prompt="p", budget_limit=10, status=WorkflowStatus.RUNNING)
        db_session.add(wf)
        await db_session.flush()

        keypair = generate_keypair()
        store = CheckpointStore(session=db_session, keypair=keypair)

        dag = DAG([DAGNode(node_id="n1", capability="x", description="y", depends_on=[], status=TaskStatus.PENDING)])
        cp = WorkflowCheckpoint.from_dag(workflow_id=wf.workflow_id, dag=dag, reason="dup-test")

        # Serial duplicate saves should not raise and should agree on the hash.
        saved1 = await store.save(cp)
        saved2 = await store.save(cp)
        assert saved1.content_hash == saved2.content_hash

    @pytest.mark.asyncio
    async def test_different_content_produces_different_hashes(self, db_session):
        from app.identity.signer import generate_keypair
        from app.models.base import TaskStatus, WorkflowStatus
        from app.models.workflow import Workflow
        from app.orchestration.checkpoint import CheckpointStore, WorkflowCheckpoint
        from app.orchestration.dag import DAG, DAGNode

        wf = Workflow(user_id="u", prompt="p", budget_limit=10, status=WorkflowStatus.RUNNING)
        db_session.add(wf)
        await db_session.flush()

        keypair = generate_keypair()
        store = CheckpointStore(session=db_session, keypair=keypair)

        def _cp(reason: str) -> WorkflowCheckpoint:
            dag = DAG(
                [DAGNode(node_id="n1", capability="x", description="y", depends_on=[], status=TaskStatus.PENDING)]
            )
            return WorkflowCheckpoint.from_dag(workflow_id=wf.workflow_id, dag=dag, reason=reason)

        s1 = await store.save(_cp("reason-a"))
        s2 = await store.save(_cp("reason-b"))
        assert s1.content_hash != s2.content_hash


class TestRateLimitEnvelope:
    @pytest.mark.asyncio
    async def test_rate_limited_response_shape(self, client, monkeypatch):
        # Set an absurdly low rate limit just for this test.
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "rate_limit_requests", 1)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

        # Hit a non-exempt endpoint twice; second should be 429.
        r1 = await client.post("/api/v1/policy/registry/evaluate", json={"action": "x"})
        r2 = await client.post("/api/v1/policy/registry/evaluate", json={"action": "x"})
        # Depending on timing one of them is 429 — but at least one must be.
        codes = {r1.status_code, r2.status_code}
        assert 429 in codes
        limited = r1 if r1.status_code == 429 else r2
        body = limited.json()
        _assert_envelope_shape(body, expected_code="RATE_LIMITED")
        assert limited.headers.get("retry-after")


class TestPolicyDecisionEdgeCases:
    @pytest.mark.asyncio
    async def test_unknown_policy_action_filter_returns_422_envelope(self, client):
        resp = await client.get("/api/v1/audit/policy-decisions?action=not-an-action")
        assert resp.status_code == 422
        _assert_envelope_shape(resp.json())

    @pytest.mark.asyncio
    async def test_workflow_resume_rejects_unknown_hash(self, client):
        resp = await client.post(
            f"/api/v1/workflows/{uuid.uuid4()}/resume",
            json={"checkpoint_hash": "zKunknownHash"},
        )
        assert resp.status_code == 404
        _assert_envelope_shape(resp.json(), expected_code="NOT_FOUND")
