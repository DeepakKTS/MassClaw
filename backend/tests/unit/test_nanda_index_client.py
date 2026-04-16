"""Unit tests for :class:`NandaIndexClient` against a mocked HTTP transport."""

from __future__ import annotations

import httpx
import pytest

from app.identity.agent_facts import AgentFacts, ToolDescriptor, build_agent_facts
from app.identity.nanda_index import (
    NandaIndexClient,
    NandaIndexConfig,
    NandaIndexInvalidResponse,
    NandaIndexNotFound,
    NandaIndexUnavailable,
)
from app.identity.signer import generate_keypair


BASE_URL = "https://index.projectnanda.test"


def _make_facts() -> tuple[AgentFacts, object]:
    kp = generate_keypair()
    facts = build_agent_facts(
        keypair=kp,
        document_id="urn:test:agent",
        name="test-agent",
        description="fixture",
        capabilities=["cap1"],
        tools=[ToolDescriptor(name="echo", description="echo")],
    )
    return facts, kp


def _client_with(handler) -> NandaIndexClient:
    transport = httpx.MockTransport(handler)
    return NandaIndexClient(
        NandaIndexConfig(base_url=BASE_URL, retry_attempts=3),
        transport=transport,
    )


@pytest.mark.asyncio
async def test_resolve_returns_embedded_document():
    facts, _ = _make_facts()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/agents/" in request.url.path
        return httpx.Response(200, json=facts.to_document())

    client = _client_with(handler)
    resolved = await client.resolve(facts.credential_subject.id)
    assert resolved is not None
    assert resolved.credential_subject.id == facts.credential_subject.id
    assert resolved.verify() is True


@pytest.mark.asyncio
async def test_resolve_follows_facts_url_pointer():
    facts, _ = _make_facts()
    did = facts.credential_subject.id

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/agents/"):
            return httpx.Response(
                200,
                json={"did": did, "facts_url": f"{BASE_URL}/well-known/{did}"},
            )
        assert str(request.url).endswith(f"/well-known/{did}")
        return httpx.Response(200, json=facts.to_document())

    client = _client_with(handler)
    resolved = await client.resolve(did)
    assert resolved is not None
    assert resolved.credential_subject.id == did


@pytest.mark.asyncio
async def test_resolve_returns_none_for_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    client = _client_with(handler)
    facts, _ = _make_facts()
    result = await client.resolve(facts.credential_subject.id)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_rejects_mismatched_subject():
    facts, _ = _make_facts()
    other_kp = generate_keypair()
    # Sign a different document and return it when we ask for our DID.
    impostor = build_agent_facts(keypair=other_kp, document_id="urn:test:imp", name="x", description="y")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=impostor.to_document())

    client = _client_with(handler)
    with pytest.raises(NandaIndexInvalidResponse):
        await client.resolve(facts.credential_subject.id)


@pytest.mark.asyncio
async def test_resolve_rejects_bad_signature():
    facts, _ = _make_facts()
    doc = facts.to_document()
    # Mutate after signing so verify() must fail.
    doc["credentialSubject"]["name"] = "TAMPERED"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=doc)

    client = _client_with(handler)
    with pytest.raises(NandaIndexInvalidResponse):
        await client.resolve(facts.credential_subject.id)


@pytest.mark.asyncio
async def test_resolve_5xx_retries_then_raises_unavailable():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(503, json={"error": "busy"})

    client = NandaIndexClient(
        NandaIndexConfig(base_url=BASE_URL, retry_attempts=3),
        transport=httpx.MockTransport(handler),
    )
    facts, _ = _make_facts()
    with pytest.raises(NandaIndexUnavailable):
        await client.resolve(facts.credential_subject.id)
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_register_sends_doc_and_url():
    facts, _ = _make_facts()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        import json as _json

        captured["body"] = _json.loads(request.content.decode())
        return httpx.Response(201, json={"did": facts.credential_subject.id, "status": "created"})

    client = _client_with(handler)
    result = await client.register(facts, facts_url="https://example.invalid/.well-known/agent-facts.json")
    assert result.did == facts.credential_subject.id
    assert captured["method"] == "POST"
    assert captured["body"]["did"] == facts.credential_subject.id
    assert captured["body"]["facts_url"].endswith("agent-facts.json")
    assert captured["body"]["agent_facts"]["proof"]["type"] == "Ed25519Signature2020"


@pytest.mark.asyncio
async def test_register_sends_bearer_token_when_configured():
    facts, _ = _make_facts()
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"did": facts.credential_subject.id})

    transport = httpx.MockTransport(handler)
    client = NandaIndexClient(
        NandaIndexConfig(base_url=BASE_URL, api_token="secret-token"),
        transport=transport,
    )
    await client.register(facts)
    assert captured["auth"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_deregister_is_idempotent_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    client = _client_with(handler)
    facts, _ = _make_facts()
    # Should not raise even though Index claims 404 — deregister is idempotent.
    await client.deregister(facts.credential_subject.id)


@pytest.mark.asyncio
async def test_resolve_rejects_malformed_did():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _client_with(handler)
    from app.identity.did import MalformedDIDError

    with pytest.raises(MalformedDIDError):
        await client.resolve("not-a-did")


@pytest.mark.asyncio
async def test_resolve_reports_non_object_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    client = _client_with(handler)
    facts, _ = _make_facts()
    with pytest.raises(NandaIndexInvalidResponse):
        await client.resolve(facts.credential_subject.id)


@pytest.mark.asyncio
async def test_resolve_raises_on_4xx_other_than_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = _client_with(handler)
    facts, _ = _make_facts()
    from app.identity.nanda_index import NandaIndexError

    with pytest.raises(NandaIndexError):
        await client.resolve(facts.credential_subject.id)


@pytest.mark.asyncio
async def test_not_found_is_distinct_error_type():
    """Ensure consumers can catch NotFound separately from Unavailable."""
    assert issubclass(NandaIndexNotFound, Exception)
    assert not issubclass(NandaIndexNotFound, NandaIndexUnavailable)
