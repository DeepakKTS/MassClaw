"""HTTP-level tests for AgentFacts endpoints.

Confirms the FastAPI app correctly wires the well-known route at the HTTP root,
exposes the per-agent AgentFacts endpoint under ``/api/v1/agents/{id}/``, and
returns a verification report from ``POST /api/v1/agents/verify-facts``.

These tests use FastAPI's ``TestClient`` with app-level overrides so they do
not depend on an actual running server. They still require the shared Postgres
+ Redis test services that the other integration tests use.
"""

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
    """Build a TestClient wired to the per-test DB and Redis fixtures.

    Overrides FastAPI ``Depends`` so request handlers pick up the in-memory
    session/redis rather than allocating fresh ones.
    """

    async def _db_override() -> AsyncIterator:
        yield db_session

    async def _redis_override() -> AsyncIterator:
        yield redis_client

    # Isolate instance key + KEK to this test so runs don't bleed into each
    # other's ``/var/lib/massclaw/instance.key`` file or cached key stores.
    monkeypatch.setenv("IDENTITY_INSTANCE_KEY_PATH", str(tmp_path / "instance.key"))
    monkeypatch.setenv("IDENTITY_KEY_ENCRYPTION_KEY", "22" * 32)
    # Bust any cached KeyStore/Settings between tests.
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


def test_well_known_returns_signed_agent_facts(test_client: TestClient) -> None:
    resp = test_client.get("/.well-known/agent-facts.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == ["VerifiableCredential", "AgentFacts"]
    assert body["issuer"].startswith("did:nanda:")
    assert body["proof"]["type"] == "Ed25519Signature2020"
    # The discovery endpoints must include the well-known path so the
    # document is self-describing without out-of-band knowledge.
    endpoints = body["credentialSubject"]["endpoints"]
    assert endpoints["agent_facts"].endswith("/.well-known/agent-facts.json")


def test_well_known_document_verifies(test_client: TestClient) -> None:
    body = test_client.get("/.well-known/agent-facts.json").json()
    report = IdentityService.verify_document(body)
    assert report["valid"] is True, report


def test_per_agent_agent_facts(test_client: TestClient, sample_agent) -> None:
    path = f"/api/v1/agents/{sample_agent.agent_id}/agent-facts.json"
    resp = test_client.get(path)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["credentialSubject"]["id"].startswith("did:nanda:")
    assert body["credentialSubject"]["name"] == sample_agent.name
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
    body["credentialSubject"]["name"] = "EVIL-MASSCLAW"
    resp = test_client.post("/api/v1/agents/verify-facts", json=body)
    assert resp.status_code == 200
    report = resp.json()
    assert report["valid"] is False
    assert report["errors"]
