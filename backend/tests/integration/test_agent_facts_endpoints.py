"""HTTP-level tests for AgentFacts endpoints (v1 schema)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi.testclient import TestClient

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.main import app
from app.services.identity_service import IdentityService


@pytest_asyncio.fixture
async def test_client(db_session, redis_client, tmp_path, monkeypatch) -> AsyncIterator[TestClient]:
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
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await asyncio.sleep(0)


def test_well_known_returns_v1_shape(test_client: TestClient) -> None:
    resp = test_client.get("/.well-known/agent-facts.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # v1 shape — flat fields, no VC envelope keys.
    assert body["id"].startswith("urn:agent:")
    assert body["agent_name"]
    assert body["label"]
    assert body["version"]
    assert body["provider"]["did"].startswith("did:key:z")
    assert isinstance(body["capabilities"], dict)
    assert "modalities" in body["capabilities"]
    assert isinstance(body["skills"], list)
    assert isinstance(body["endpoints"]["static"], list)
    # Old VC keys must not appear.
    for forbidden in ("@context", "credentialSubject", "proof", "validFrom", "issuer"):
        assert forbidden not in body


def test_well_known_document_verifies(test_client: TestClient) -> None:
    body = test_client.get("/.well-known/agent-facts.json").json()
    report = IdentityService.verify_document(body)
    assert report["valid"] is True, report


def test_per_agent_agent_facts(test_client: TestClient, sample_agent) -> None:
    path = f"/api/v1/agents/{sample_agent.agent_id}/agent-facts.json"
    resp = test_client.get(path)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"]["did"].startswith("did:key:z")
    assert body["label"] == sample_agent.name
    assert IdentityService.verify_document(body)["valid"] is True


def test_per_agent_unknown_returns_404(test_client: TestClient) -> None:
    import uuid

    resp = test_client.get(f"/api/v1/agents/{uuid.uuid4()}/agent-facts.json")
    assert resp.status_code == 404


def test_verify_endpoint_accepts_valid_document(test_client: TestClient) -> None:
    body = test_client.get("/.well-known/agent-facts.json").json()
    resp = test_client.post("/api/v1/agents/verify-facts", json=body)
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_verify_endpoint_catches_tampering(test_client: TestClient) -> None:
    body = test_client.get("/.well-known/agent-facts.json").json()
    body["label"] = "EVIL-MASSCLAW"
    resp = test_client.post("/api/v1/agents/verify-facts", json=body)
    assert resp.status_code == 200
    report = resp.json()
    assert report["valid"] is False
    assert report["errors"]
