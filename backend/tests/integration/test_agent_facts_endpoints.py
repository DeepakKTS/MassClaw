"""HTTP-level tests for AgentFacts endpoints (v1 schema).

Uses :class:`httpx.AsyncClient` with :class:`httpx.ASGITransport` so the
test and the FastAPI app share the same asyncio event loop — this is
required because our DB dependency override yields an async
:class:`AsyncSession` that is bound to the test's loop. Using the sync
``starlette.testclient.TestClient`` runs the app in a separate thread/loop
and produces ``Future attached to a different loop`` crashes on the
first ORM call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.main import app
from app.services.identity_service import IdentityService


@pytest_asyncio.fixture
async def client(db_session, redis_client, tmp_path, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    """Async client bound to the app, sharing the test's event loop."""

    async def _db_override() -> AsyncIterator:
        yield db_session

    async def _redis_override() -> AsyncIterator:
        yield redis_client

    monkeypatch.setenv("IDENTITY_INSTANCE_KEY_PATH", str(tmp_path / "instance.key"))
    monkeypatch.setenv("IDENTITY_KEY_ENCRYPTION_KEY", "22" * 32)

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


async def test_well_known_returns_v1_shape(client: httpx.AsyncClient) -> None:
    resp = await client.get("/.well-known/agent-facts.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"].startswith("urn:agent:")
    assert body["agent_name"]
    assert body["label"]
    assert body["version"]
    assert body["provider"]["did"].startswith("did:key:z")
    assert isinstance(body["capabilities"], dict)
    assert "modalities" in body["capabilities"]
    assert isinstance(body["skills"], list)
    assert isinstance(body["endpoints"]["static"], list)
    for forbidden in ("@context", "credentialSubject", "proof", "validFrom", "issuer"):
        assert forbidden not in body


async def test_well_known_document_verifies(client: httpx.AsyncClient) -> None:
    body = (await client.get("/.well-known/agent-facts.json")).json()
    report = IdentityService.verify_document(body)
    assert report["valid"] is True, report


async def test_per_agent_agent_facts(client: httpx.AsyncClient, sample_agent) -> None:
    resp = await client.get(f"/api/v1/agents/{sample_agent.agent_id}/agent-facts.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"]["did"].startswith("did:key:z")
    assert body["label"] == sample_agent.name
    assert IdentityService.verify_document(body)["valid"] is True


async def test_per_agent_unknown_returns_404(client: httpx.AsyncClient) -> None:
    import uuid

    resp = await client.get(f"/api/v1/agents/{uuid.uuid4()}/agent-facts.json")
    assert resp.status_code == 404


async def test_verify_endpoint_accepts_valid_document(client: httpx.AsyncClient) -> None:
    body = (await client.get("/.well-known/agent-facts.json")).json()
    resp = await client.post("/api/v1/agents/verify-facts", json=body)
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


async def test_verify_endpoint_catches_tampering(client: httpx.AsyncClient) -> None:
    body = (await client.get("/.well-known/agent-facts.json")).json()
    body["label"] = "EVIL-MASSCLAW"
    resp = await client.post("/api/v1/agents/verify-facts", json=body)
    assert resp.status_code == 200
    report = resp.json()
    assert report["valid"] is False
    assert report["errors"]
