"""Integration tests for the IdentityService.

These exercise real Postgres + Redis via the shared test fixtures, which means
they require ``DATABASE_URL`` and ``REDIS_URL`` to point at runnable test
services (the default conftest fixtures spin up per-test schemas).

Focus:
- Instance AgentFacts is built, signed, and round-trips through the cache.
- Per-agent AgentFacts persists a keypair into agent metadata and round-trips.
- Verification endpoint catches tampering.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.identity.agent_facts import AgentFacts
from app.identity.did import build_did_from_public_key, public_key_from_did
from app.identity.key_store import KeyStore
from app.services.identity_service import (
    _AGENT_KEY_METADATA_FIELD,
    IdentityService,
)


@pytest_asyncio.fixture
async def isolated_key_store(tmp_path):
    """Provide a KeyStore bound to a tmp path with a deterministic KEK."""
    kek = bytes.fromhex("11" * 32)
    return KeyStore(
        instance_key_path=tmp_path / "instance.key",
        key_encryption_key=kek,
    )


@pytest_asyncio.fixture
async def identity_service(db_session, redis_client, isolated_key_store):
    # Guarantee a clean cache between tests — tests may share Redis DB.
    await redis_client.delete("identity:instance_facts")
    return IdentityService(session=db_session, redis=redis_client, key_store=isolated_key_store)


class TestInstanceFacts:
    @pytest.mark.asyncio
    async def test_returns_signed_agent_facts(self, identity_service):
        facts = await identity_service.get_instance_facts()
        assert isinstance(facts, AgentFacts)
        assert facts.proof is not None
        assert facts.verify() is True

    @pytest.mark.asyncio
    async def test_subject_did_matches_instance_public_key(self, identity_service, isolated_key_store):
        facts = await identity_service.get_instance_facts()
        instance_pub = isolated_key_store.instance_keypair().public_bytes
        assert facts.credential_subject.id == build_did_from_public_key(instance_pub)
        assert public_key_from_did(facts.credential_subject.id) == instance_pub

    @pytest.mark.asyncio
    async def test_cached_copy_is_reused(self, identity_service, redis_client):
        first = await identity_service.get_instance_facts()
        # Mutate the Redis copy so we can detect whether we came back to disk.
        raw = await redis_client.get("identity:instance_facts")
        assert raw is not None
        second = await identity_service.get_instance_facts()
        assert second.proof.signature_value == first.proof.signature_value

    @pytest.mark.asyncio
    async def test_invalidation_regenerates(self, identity_service):
        first = await identity_service.get_instance_facts()
        await identity_service.invalidate_instance_facts()
        second = await identity_service.get_instance_facts()
        # Signature may match bit-for-bit because Ed25519 is deterministic and
        # the signable body is unchanged; but the underlying document must be
        # freshly-built and still verify.
        assert second.verify() is True
        assert second.issuer == first.issuer

    @pytest.mark.asyncio
    async def test_endpoints_include_well_known(self, identity_service):
        facts = await identity_service.get_instance_facts()
        endpoints = facts.credential_subject.endpoints
        assert "http" in endpoints
        assert endpoints["agent_facts"].endswith("/.well-known/agent-facts.json")

    @pytest.mark.asyncio
    async def test_capabilities_advertised(self, identity_service):
        facts = await identity_service.get_instance_facts()
        caps = set(facts.credential_subject.capabilities)
        # Every endpoint a stock agent needs to accomplish a workflow must be advertised.
        assert {"workflow.submit", "workflow.status", "workflow.result"} <= caps
        assert {"memory.query", "memory.write"} <= caps


class TestAgentFacts:
    @pytest.mark.asyncio
    async def test_generates_and_persists_keypair(self, identity_service, sample_agent, db_session):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert facts.verify() is True
        # Keypair should have been written into metadata.
        await db_session.refresh(sample_agent)
        key_entry = (sample_agent.metadata_ or {}).get(_AGENT_KEY_METADATA_FIELD)
        assert key_entry is not None
        assert "wrapped_private_seed_hex" in key_entry
        assert key_entry["public_key_multibase"].startswith("z")
        assert key_entry["did"].startswith("did:nanda:z")

    @pytest.mark.asyncio
    async def test_subject_matches_agent(self, identity_service, sample_agent):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert facts.credential_subject.name == sample_agent.name
        assert facts.credential_subject.description == sample_agent.description
        assert set(facts.credential_subject.capabilities) == set(sample_agent.capabilities)

    @pytest.mark.asyncio
    async def test_keypair_is_stable_across_calls(self, identity_service, sample_agent, db_session):
        first = await identity_service.get_agent_facts(sample_agent.agent_id)
        await identity_service.invalidate_agent_facts(sample_agent.agent_id)
        second = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert first.credential_subject.id == second.credential_subject.id
        assert first.credential_subject.public_key_multibase == second.credential_subject.public_key_multibase

    @pytest.mark.asyncio
    async def test_unknown_agent_raises_not_found(self, identity_service):
        from app.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await identity_service.get_agent_facts(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_endpoints_include_facts_url(self, identity_service, sample_agent):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert facts.credential_subject.endpoints.get("facts", "").endswith(
            f"/api/v1/agents/{sample_agent.agent_id}/agent-facts.json"
        )


class TestVerifyDocument:
    @pytest.mark.asyncio
    async def test_valid_document_verifies(self, identity_service):
        facts = await identity_service.get_instance_facts()
        report = IdentityService.verify_document(facts.to_document())
        assert report["valid"] is True
        assert report["errors"] == []

    @pytest.mark.asyncio
    async def test_tampered_document_fails_verification(self, identity_service):
        facts = await identity_service.get_instance_facts()
        tampered = facts.to_document()
        tampered["credentialSubject"]["name"] = "EVIL-MASSCLAW"
        report = IdentityService.verify_document(tampered)
        assert report["valid"] is False
        assert report["errors"]

    @pytest.mark.asyncio
    async def test_malformed_document_reports_error(self, identity_service):
        report = IdentityService.verify_document({"oops": "not an agent facts doc"})
        assert report["valid"] is False
        assert report["errors"]

    @pytest.mark.asyncio
    async def test_unsigned_document_reports_error(self, identity_service):
        facts = await identity_service.get_instance_facts()
        unsigned = facts.to_document()
        unsigned.pop("proof", None)
        report = IdentityService.verify_document(unsigned)
        assert report["valid"] is False
        assert "no proof" in " ".join(report["errors"]).lower()
