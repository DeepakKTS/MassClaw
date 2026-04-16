"""Unit tests for :class:`DIDResolver` — the three-strategy resolution pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from app.identity.agent_facts import AgentFacts, build_agent_facts
from app.identity.did import MalformedDIDError
from app.identity.did_resolver import DIDResolver, ResolutionError
from app.identity.signer import generate_keypair


def _facts() -> AgentFacts:
    return build_agent_facts(
        keypair=generate_keypair(),
        document_id="urn:test:1",
        name="test",
        description="fixture",
    )


class _StubLocal:
    def __init__(self, doc: AgentFacts | None = None) -> None:
        self.doc = doc

    async def get_agent_facts_by_did(self, did: str) -> AgentFacts | None:
        if self.doc is None:
            return None
        if self.doc.credential_subject.id != did:
            return None
        return self.doc


class _StubIndex:
    def __init__(self, doc: AgentFacts | None = None) -> None:
        self.doc = doc
        self.calls = 0

    async def resolve(self, did: str) -> AgentFacts | None:
        self.calls += 1
        if self.doc is None or self.doc.credential_subject.id != did:
            return None
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
async def test_local_wins_over_index():
    doc = _facts()
    local = _StubLocal(doc)
    index = _StubIndex(doc)
    resolver = DIDResolver(local_registry=local, nanda_index=index)
    result = await resolver.resolve(doc.credential_subject.id)
    assert result.source == "local"
    assert index.calls == 0


@pytest.mark.asyncio
async def test_index_used_when_local_empty():
    doc = _facts()
    local = _StubLocal(None)
    index = _StubIndex(doc)
    resolver = DIDResolver(local_registry=local, nanda_index=index)
    result = await resolver.resolve(doc.credential_subject.id)
    assert result.source == "nanda_index"
    assert index.calls == 1


@pytest.mark.asyncio
async def test_well_known_used_when_both_miss():
    doc = _facts()
    local = _StubLocal(None)
    index = _StubIndex(None)
    well = _StubWellKnown(doc)
    resolver = DIDResolver(local_registry=local, nanda_index=index, well_known_fetcher=well)
    result = await resolver.resolve(
        doc.credential_subject.id,
        well_known_url_hint="https://example.invalid/.well-known/agent-facts.json",
    )
    assert result.source == "well_known"
    assert well.last_url == "https://example.invalid/.well-known/agent-facts.json"


@pytest.mark.asyncio
async def test_all_miss_raises():
    doc = _facts()
    resolver = DIDResolver(
        local_registry=_StubLocal(None),
        nanda_index=_StubIndex(None),
        well_known_fetcher=_StubWellKnown(None),
    )
    with pytest.raises(ResolutionError):
        await resolver.resolve(doc.credential_subject.id)


@pytest.mark.asyncio
async def test_well_known_skipped_without_hint():
    doc = _facts()
    well = _StubWellKnown(doc)
    resolver = DIDResolver(local_registry=None, nanda_index=None, well_known_fetcher=well)
    with pytest.raises(ResolutionError):
        await resolver.resolve(doc.credential_subject.id)
    assert well.calls == 0  # Never called without a hint URL.


@pytest.mark.asyncio
async def test_malformed_did_raises_malformed():
    resolver = DIDResolver()
    with pytest.raises(MalformedDIDError):
        await resolver.resolve("not-a-did")


@pytest.mark.asyncio
async def test_well_known_doc_must_match_requested_did():
    wanted = _facts()
    other = _facts()  # different DID
    well = _StubWellKnown(other)
    resolver = DIDResolver(well_known_fetcher=well)
    with pytest.raises(ResolutionError):
        await resolver.resolve(
            wanted.credential_subject.id,
            well_known_url_hint="https://example.invalid/.well-known/agent-facts.json",
        )


@pytest.mark.asyncio
async def test_local_is_tried_only_for_matching_did():
    mine = _facts()
    not_mine = _facts()
    local = _StubLocal(mine)
    resolver = DIDResolver(local_registry=local)
    # Asking for someone else's DID must not return mine.
    with pytest.raises(ResolutionError):
        await resolver.resolve(not_mine.credential_subject.id)


@pytest.mark.asyncio
async def test_non_nanda_method_skips_nanda_index():
    doc = _facts()

    class _WrongDIDLocal:
        async def get_agent_facts_by_did(self, did: str) -> AgentFacts | None:
            return None

    index = _StubIndex(doc)
    resolver = DIDResolver(local_registry=_WrongDIDLocal(), nanda_index=index)
    with pytest.raises(ResolutionError):
        await resolver.resolve("did:web:example.com")
    assert index.calls == 0  # Non-nanda DIDs must not be sent to the NANDA Index.


@pytest.mark.asyncio
async def test_tampered_document_from_local_rejected():
    doc = _facts()
    doc.credential_subject.name = "MUTATED"  # breaks signature
    local = _StubLocal(doc)
    resolver = DIDResolver(local_registry=local)
    with pytest.raises(ResolutionError):
        await resolver.resolve(doc.credential_subject.id)
