"""Unit tests for :class:`DIDResolver` — three-strategy resolution pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from app.identity.agent_facts import (
    AgentFacts,
    AgentFactsBuilder,
    Skill,
)
from app.identity.did import MalformedDIDError, build_did_key, build_did_web
from app.identity.did_resolver import DIDResolver, ResolutionError
from app.identity.signer import generate_keypair


def _signed_doc(provider_did_override: str | None = None):
    kp = generate_keypair()
    did = provider_did_override or build_did_key(kp.public_bytes)
    b = (
        AgentFactsBuilder(
            agent_id="urn:agent:massclaw:fixture",
            label="fixture",
            description="resolver fixture",
            version="1.0.0",
            provider_name="MassClaw",
            provider_url="https://example.test",
        )
        .provider_did(did)
        .endpoint("https://example.test/api/v1")
        .modalities("text")
        .auth_methods("bearer")
        .add_skill(Skill(id="x", description="x", **{"inputModes": ["text"], "outputModes": ["text"]}))
    )
    doc = b.build_and_sign(kp)
    return doc, kp, did


def _unsigned_doc(provider_did: str | None = None) -> AgentFacts:
    kp = generate_keypair()
    b = (
        AgentFactsBuilder(
            agent_id="urn:agent:massclaw:unsigned",
            label="unsigned",
            description="unsigned fixture",
            version="1.0.0",
            provider_name="MassClaw",
            provider_url="https://example.test",
        )
        .provider_did(provider_did or build_did_key(kp.public_bytes))
        .endpoint("https://example.test/api/v1")
        .modalities("text")
        .auth_methods("bearer")
        .add_skill(Skill(id="x", description="x", **{"inputModes": ["text"], "outputModes": ["text"]}))
    )
    return b.build()  # no integrity credential


class _StubLocal:
    def __init__(self, doc: AgentFacts | None = None, match_did: str | None = None) -> None:
        self.doc = doc
        self.match_did = match_did

    async def get_agent_facts_by_did(self, did: str) -> AgentFacts | None:
        if self.doc is None:
            return None
        if self.match_did and self.match_did != did:
            return None
        return self.doc


class _StubIndex:
    def __init__(self, doc: AgentFacts | None = None) -> None:
        self.doc = doc
        self.calls = 0

    async def resolve_by_username(self, username: str) -> AgentFacts | None:
        self.calls += 1
        return self.doc


class _StubWellKnown:
    def __init__(self, doc: AgentFacts | None = None) -> None:
        self.doc = doc
        self.calls = 0
        self.last_url: str | None = None

    async def fetch(self, url: str) -> dict[str, Any] | None:
        self.calls += 1
        self.last_url = url
        if self.doc is None:
            return None
        return self.doc.to_document()


@pytest.mark.asyncio
async def test_local_wins_when_did_matches():
    doc, _, did = _signed_doc()
    local = _StubLocal(doc, match_did=did)
    index = _StubIndex(doc)
    resolver = DIDResolver(local_registry=local, nanda_index=index)
    result = await resolver.resolve(did, username_hint="alpha")
    assert result.source == "local"
    assert index.calls == 0


@pytest.mark.asyncio
async def test_index_used_when_local_empty_and_username_given():
    doc, _, did = _signed_doc()
    resolver = DIDResolver(local_registry=_StubLocal(None), nanda_index=_StubIndex(doc))
    result = await resolver.resolve(did, username_hint="alpha")
    assert result.source == "nanda_index"


@pytest.mark.asyncio
async def test_index_skipped_without_username_hint():
    doc, _, did = _signed_doc()
    index = _StubIndex(doc)
    resolver = DIDResolver(local_registry=_StubLocal(None), nanda_index=index)
    with pytest.raises(ResolutionError):
        await resolver.resolve(did)
    assert index.calls == 0


@pytest.mark.asyncio
async def test_well_known_used_when_both_miss():
    doc, _, did = _signed_doc()
    well = _StubWellKnown(doc)
    resolver = DIDResolver(
        local_registry=_StubLocal(None),
        nanda_index=_StubIndex(None),
        well_known_fetcher=well,
    )
    result = await resolver.resolve(
        did,
        username_hint="alpha",
        well_known_url_hint="https://example.test/.well-known/agent-facts.json",
    )
    assert result.source == "well_known"
    assert well.last_url == "https://example.test/.well-known/agent-facts.json"


@pytest.mark.asyncio
async def test_all_miss_raises():
    _, _, did = _signed_doc()
    resolver = DIDResolver(
        local_registry=_StubLocal(None),
        nanda_index=_StubIndex(None),
        well_known_fetcher=_StubWellKnown(None),
    )
    with pytest.raises(ResolutionError):
        await resolver.resolve(did, username_hint="alpha")


@pytest.mark.asyncio
async def test_malformed_did_raises():
    resolver = DIDResolver()
    with pytest.raises(MalformedDIDError):
        await resolver.resolve("not-a-did")


@pytest.mark.asyncio
async def test_unsigned_doc_accepted_when_provider_did_matches():
    """Section 7 of the resolver contract: without credentials, provider.did
    must equal the expected DID for the doc to be accepted."""
    kp = generate_keypair()
    did = build_did_key(kp.public_bytes)
    doc = _unsigned_doc(provider_did=did)
    resolver = DIDResolver(local_registry=_StubLocal(doc, match_did=did))
    result = await resolver.resolve(did)
    assert result.source == "local"


@pytest.mark.asyncio
async def test_unsigned_doc_rejected_when_provider_did_mismatches():
    kp = generate_keypair()
    wrong_did = build_did_key(kp.public_bytes)
    doc = _unsigned_doc(provider_did=build_did_web("example.com"))
    resolver = DIDResolver(local_registry=_StubLocal(doc, match_did=wrong_did))
    with pytest.raises(ResolutionError):
        await resolver.resolve(wrong_did)


@pytest.mark.asyncio
async def test_tampered_signed_doc_rejected():
    doc, _, did = _signed_doc()
    doc.label = "TAMPERED"  # breaks signature
    resolver = DIDResolver(local_registry=_StubLocal(doc, match_did=did))
    with pytest.raises(ResolutionError):
        await resolver.resolve(did)


@pytest.mark.asyncio
async def test_did_web_accepted_when_provider_matches():
    """did:web doesn't carry a public key, so we rely on provider.did equality."""
    web_did = build_did_web("example.com")
    doc = _unsigned_doc(provider_did=web_did)
    resolver = DIDResolver(local_registry=_StubLocal(doc, match_did=web_did))
    result = await resolver.resolve(web_did)
    assert result.source == "local"
