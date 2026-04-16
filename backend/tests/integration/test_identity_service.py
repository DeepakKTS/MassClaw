"""Integration tests for the IdentityService.

These exercise real Postgres + Redis via the shared test fixtures, which means
they require ``DATABASE_URL`` and ``REDIS_URL`` to point at runnable test
services (CI provides both via docker services).

Focus:
- Instance AgentFacts v1 document is built, signed, and round-trips through cache.
- Per-agent AgentFacts persists a keypair into agent metadata and round-trips.
- Verification catches tampering.
- DID resolver uses the local registry adapter.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.identity.agent_facts import AgentFacts
from app.identity.did import build_did_key, decode_did_key
from app.identity.key_store import KeyStore
from app.services.identity_service import (
    _AGENT_KEY_METADATA_FIELD,
    IdentityService,
)


@pytest_asyncio.fixture
async def isolated_key_store(tmp_path):
    kek = bytes.fromhex("11" * 32)
    return KeyStore(
        instance_key_path=tmp_path / "instance.key",
        key_encryption_key=kek,
    )


@pytest_asyncio.fixture
async def identity_service(db_session, redis_client, isolated_key_store):
    await redis_client.delete("identity:instance_facts")
    return IdentityService(session=db_session, redis=redis_client, key_store=isolated_key_store)


class TestInstanceFacts:
    @pytest.mark.asyncio
    async def test_returns_signed_v1_document(self, identity_service):
        facts = await identity_service.get_instance_facts()
        assert isinstance(facts, AgentFacts)
        assert facts.provider.did is not None
        assert facts.provider.did.startswith("did:key:z")
        assert facts.label
        assert facts.skills
        assert len(facts.verifiable_credentials) == 1
        assert facts.verify_integrity() is True

    @pytest.mark.asyncio
    async def test_provider_did_matches_instance_key(self, identity_service, isolated_key_store):
        facts = await identity_service.get_instance_facts()
        instance_pub = isolated_key_store.instance_keypair().public_bytes
        assert facts.provider.did == build_did_key(instance_pub)
        assert decode_did_key(facts.provider.did) == instance_pub

    @pytest.mark.asyncio
    async def test_cached_copy_is_reused(self, identity_service, redis_client):
        first = await identity_service.get_instance_facts()
        raw = await redis_client.get("identity:instance_facts")
        assert raw is not None
        second = await identity_service.get_instance_facts()
        assert second.verifiable_credentials[0].proof.proof_value == first.verifiable_credentials[0].proof.proof_value

    @pytest.mark.asyncio
    async def test_invalidation_regenerates(self, identity_service):
        first = await identity_service.get_instance_facts()
        await identity_service.invalidate_instance_facts()
        second = await identity_service.get_instance_facts()
        assert second.verify_integrity() is True
        assert second.provider.did == first.provider.did

    @pytest.mark.asyncio
    async def test_endpoints_include_well_known(self, identity_service):
        facts = await identity_service.get_instance_facts()
        endpoints = facts.endpoints.static
        assert any(url.endswith("/.well-known/agent-facts.json") for url in endpoints)

    @pytest.mark.asyncio
    async def test_skills_advertised(self, identity_service):
        facts = await identity_service.get_instance_facts()
        ids = {s.id for s in facts.skills}
        assert {"workflow.submit", "workflow.status", "workflow.result"} <= ids
        assert {"memory.query", "memory.write"} <= ids


class TestAgentFacts:
    @pytest.mark.asyncio
    async def test_generates_and_persists_keypair(self, identity_service, sample_agent, db_session):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert facts.verify_integrity() is True
        await db_session.refresh(sample_agent)
        key_entry = (sample_agent.metadata_ or {}).get(_AGENT_KEY_METADATA_FIELD)
        assert key_entry is not None
        assert "wrapped_private_seed_hex" in key_entry
        assert key_entry["public_key_multibase"].startswith("z")
        assert key_entry["did"].startswith("did:key:z")

    @pytest.mark.asyncio
    async def test_subject_matches_agent(self, identity_service, sample_agent):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert facts.label == sample_agent.name
        assert facts.description == sample_agent.description

    @pytest.mark.asyncio
    async def test_keypair_stable_across_cache_invalidation(self, identity_service, sample_agent):
        first = await identity_service.get_agent_facts(sample_agent.agent_id)
        await identity_service.invalidate_agent_facts(sample_agent.agent_id)
        second = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert first.provider.did == second.provider.did

    @pytest.mark.asyncio
    async def test_unknown_agent_raises(self, identity_service):
        from app.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await identity_service.get_agent_facts(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_endpoints_include_agent_url(self, identity_service, sample_agent):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        assert any(url.endswith(f"/api/v1/agents/{sample_agent.agent_id}") for url in facts.endpoints.static)


class TestVerifyDocument:
    @pytest.mark.asyncio
    async def test_valid_document(self, identity_service):
        facts = await identity_service.get_instance_facts()
        report = IdentityService.verify_document(facts.to_document())
        assert report["valid"] is True

    @pytest.mark.asyncio
    async def test_tampered_document(self, identity_service):
        facts = await identity_service.get_instance_facts()
        tampered = facts.to_document()
        tampered["label"] = "EVIL-MASSCLAW"
        report = IdentityService.verify_document(tampered)
        assert report["valid"] is False
        assert report["errors"]

    @pytest.mark.asyncio
    async def test_malformed_document(self, identity_service):
        report = IdentityService.verify_document({"oops": "not an agent facts doc"})
        assert report["valid"] is False
        assert report["errors"]

    @pytest.mark.asyncio
    async def test_document_without_credentials(self, identity_service):
        facts = await identity_service.get_instance_facts()
        doc = facts.to_document()
        doc.pop("verifiable_credentials", None)
        report = IdentityService.verify_document(doc)
        assert report["valid"] is False
        assert any("verifiable_credentials" in e for e in report["errors"])


class TestResolveDid:
    @pytest.mark.asyncio
    async def test_local_lookup_resolves_registered_agent(self, identity_service, sample_agent, redis_client):
        facts = await identity_service.get_agent_facts(sample_agent.agent_id)
        await redis_client.delete(f"identity:resolve:{facts.provider.did}")
        resolved = await identity_service.resolve_did(facts.provider.did)
        assert resolved.source in {"local", "cache"}
        assert resolved.agent_facts.provider.did == facts.provider.did
