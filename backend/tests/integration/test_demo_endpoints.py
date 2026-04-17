"""HTTP-level tests for the demo-only endpoints.

Gated behind ``MASSCLAW_DEMO_MODE``:

- When false (production default) every /demo/* route returns 404.
- When true (federation docker-compose) the routes become available and
  actually sign / seed.

Uses :class:`httpx.AsyncClient` + :class:`httpx.ASGITransport` so the
async ORM session stays on the test's event loop — same pattern as
``test_agent_facts_endpoints.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.main import app


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


class TestDemoGating:
    async def test_seed_workflow_404_when_demo_disabled(self, client: httpx.AsyncClient, monkeypatch) -> None:
        monkeypatch.delenv("MASSCLAW_DEMO_MODE", raising=False)
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        resp = await client.post(
            "/api/v1/demo/seed-workflow",
            json={"workflow_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "demo_endpoints_disabled"

    async def test_self_sign_write_404_when_demo_disabled(self, client: httpx.AsyncClient, monkeypatch) -> None:
        monkeypatch.delenv("MASSCLAW_DEMO_MODE", raising=False)
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        resp = await client.post(
            "/api/v1/demo/self-sign-write",
            json={"workflow_id": str(uuid.uuid4()), "content": "x"},
        )
        assert resp.status_code == 404


class TestSeedWorkflowEnabled:
    async def test_idempotent_seed(self, client: httpx.AsyncClient, monkeypatch) -> None:
        monkeypatch.setenv("MASSCLAW_DEMO_MODE", "true")
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        wf_id = str(uuid.uuid4())
        first = await client.post("/api/v1/demo/seed-workflow", json={"workflow_id": wf_id})
        assert first.status_code == 200
        assert first.json()["created"] is True

        second = await client.post("/api/v1/demo/seed-workflow", json={"workflow_id": wf_id})
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["workflow_id"] == wf_id

    async def test_rejects_invalid_uuid(self, client: httpx.AsyncClient, monkeypatch) -> None:
        monkeypatch.setenv("MASSCLAW_DEMO_MODE", "true")
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        resp = await client.post("/api/v1/demo/seed-workflow", json={"workflow_id": "not-a-uuid"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_workflow_id"


class TestSelfSignWriteEnabled:
    async def test_signs_and_persists(self, client: httpx.AsyncClient, monkeypatch) -> None:
        monkeypatch.setenv("MASSCLAW_DEMO_MODE", "true")
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        wf_id = str(uuid.uuid4())
        await client.post("/api/v1/demo/seed-workflow", json={"workflow_id": wf_id})

        resp = await client.post(
            "/api/v1/demo/self-sign-write",
            json={
                "workflow_id": wf_id,
                "content": "deadline: April 30",
                "confidence": 0.9,
                "metadata": {"demo": True},
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["content"] == "deadline: April 30"
        assert body["author_did"].startswith("did:key:z")
        assert body["signature"] is not None
        assert body["hash"].startswith("z")
        assert body["record_state"] == "active"

    async def test_missing_content_rejected(self, client: httpx.AsyncClient, monkeypatch) -> None:
        monkeypatch.setenv("MASSCLAW_DEMO_MODE", "true")
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        wf_id = str(uuid.uuid4())
        await client.post("/api/v1/demo/seed-workflow", json={"workflow_id": wf_id})

        resp = await client.post(
            "/api/v1/demo/self-sign-write",
            json={"workflow_id": wf_id, "content": ""},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "missing_content"

    async def test_records_are_verifiable_on_read(self, client: httpx.AsyncClient, monkeypatch) -> None:
        """A self-signed record must round-trip through the by-hash endpoint cleanly."""
        monkeypatch.setenv("MASSCLAW_DEMO_MODE", "true")
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        wf_id = str(uuid.uuid4())
        await client.post("/api/v1/demo/seed-workflow", json={"workflow_id": wf_id})

        write = await client.post(
            "/api/v1/demo/self-sign-write",
            json={"workflow_id": wf_id, "content": "budget: $500"},
        )
        assert write.status_code == 201
        content_hash = write.json()["hash"]

        fetched = await client.get(f"/api/v1/memory/by-hash/{content_hash}")
        assert fetched.status_code == 200
        assert fetched.json()["content"] == "budget: $500"
