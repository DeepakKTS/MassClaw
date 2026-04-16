"""Unit tests for :class:`NandaIndexClient` against the real deployed routes.

Uses ``httpx.MockTransport`` so no network IO occurs. The tested routes match
``projnanda/nanda-index-frontend`` — list, register, update, delete — plus the
well-known fetch that completes a username-based resolution.
"""

from __future__ import annotations

import httpx
import pytest

from app.identity.agent_facts import (
    AgentFacts,
    AgentFactsBuilder,
    Skill,
)
from app.identity.did import build_did_key
from app.identity.nanda_index import (
    NandaIndexClient,
    NandaIndexConfig,
    NandaIndexInvalidResponse,
    NandaIndexNotFound,
    NandaIndexUnauthorized,
    NandaIndexUnavailable,
)
from app.identity.signer import generate_keypair

BASE_URL = "https://index.projnanda.test"
FACTS_HOST = "https://massclaw.test"
FACTS_URL = f"{FACTS_HOST}/.well-known/agent-facts.json"


def _facts(label: str = "massclaw-test") -> tuple[AgentFacts, object]:
    kp = generate_keypair()
    did = build_did_key(kp.public_bytes)
    builder = (
        AgentFactsBuilder(
            agent_id=f"urn:agent:massclaw:{label}",
            label=label,
            description="fixture agent",
            version="1.0.0",
            provider_name="MassClaw",
            provider_url="https://massclaw.test",
        )
        .provider_did(did)
        .endpoint("https://massclaw.test/api/v1")
        .modalities("text")
        .auth_methods("bearer")
        .add_skill(
            Skill(
                id="workflow.submit",
                description="submit a workflow",
                **{"inputModes": ["text"], "outputModes": ["text"]},
            )
        )
    )
    doc = builder.build_and_sign(kp)
    return doc, kp


def _client(handler, *, cookie: str | None = None, token: str | None = None) -> NandaIndexClient:
    return NandaIndexClient(
        NandaIndexConfig(
            base_url=BASE_URL,
            retry_attempts=3,
            session_cookie=cookie,
            api_token=token,
        ),
        transport=httpx.MockTransport(handler),
    )


def _mongo_entry(username: str, facts_url: str, mongo_id: str = "507f191e810c19729de860ea") -> dict:
    return {"_id": mongo_id, "username": username, "agent_facts_link": facts_url}


# ---------------------------------------------------------------- Listing


@pytest.mark.asyncio
async def test_list_agents_parses_mongo_entries():
    facts, _ = _facts()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/agents"
        return httpx.Response(200, json=[_mongo_entry("alpha", FACTS_URL, "507f191e810c19729de860ea")])

    client = _client(handler)
    rows = await client.list_agents()
    assert len(rows) == 1
    assert rows[0].username == "alpha"
    assert rows[0].mongo_id == "507f191e810c19729de860ea"
    assert rows[0].agent_facts_link == FACTS_URL


@pytest.mark.asyncio
async def test_list_agents_rejects_malformed_entries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"username": "no_id_here"}])

    client = _client(handler)
    with pytest.raises(NandaIndexInvalidResponse):
        await client.list_agents()


@pytest.mark.asyncio
async def test_find_by_username_matches():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_mongo_entry("alpha", FACTS_URL), _mongo_entry("beta", FACTS_URL + "?b", "507f1f77bcf86cd799439011")],
        )

    client = _client(handler)
    hit = await client.find_by_username("beta")
    assert hit is not None
    assert hit.username == "beta"
    assert hit.mongo_id == "507f1f77bcf86cd799439011"


@pytest.mark.asyncio
async def test_find_by_username_not_found_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_mongo_entry("alpha", FACTS_URL)])

    client = _client(handler)
    assert await client.find_by_username("does-not-exist") is None


# ---------------------------------------------------------------- Resolve


@pytest.mark.asyncio
async def test_resolve_by_username_fetches_facts_url():
    facts, _ = _facts()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agents":
            return httpx.Response(200, json=[_mongo_entry("alpha", FACTS_URL)])
        assert str(request.url) == FACTS_URL
        return httpx.Response(200, json=facts.to_document())

    client = _client(handler)
    resolved = await client.resolve_by_username("alpha")
    assert resolved is not None
    assert resolved.label == facts.label
    assert resolved.verify_integrity() is True


@pytest.mark.asyncio
async def test_resolve_by_username_returns_none_when_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client(handler)
    assert await client.resolve_by_username("ghost") is None


@pytest.mark.asyncio
async def test_resolve_raises_if_facts_url_404():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agents":
            return httpx.Response(200, json=[_mongo_entry("alpha", FACTS_URL)])
        return httpx.Response(404)

    client = _client(handler)
    with pytest.raises(NandaIndexInvalidResponse):
        await client.resolve_by_username("alpha")


@pytest.mark.asyncio
async def test_resolve_raises_for_malformed_facts_body():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agents":
            return httpx.Response(200, json=[_mongo_entry("alpha", FACTS_URL)])
        return httpx.Response(200, json={"not": "an agent facts doc"})

    client = _client(handler)
    with pytest.raises(NandaIndexInvalidResponse):
        await client.resolve_by_username("alpha")


# ---------------------------------------------------------------- Register / update / delete


@pytest.mark.asyncio
async def test_register_posts_pointer_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = _json.loads(request.content.decode())
        return httpx.Response(
            201, json={"_id": "507f1f77bcf86cd799439011", "username": "alpha", "agent_facts_link": FACTS_URL}
        )

    client = _client(handler)
    result = await client.register(username="alpha", agent_facts_link=FACTS_URL)
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/agents"
    assert captured["body"] == {"username": "alpha", "agent_facts_link": FACTS_URL}
    assert result.mongo_id == "507f1f77bcf86cd799439011"
    assert result.username == "alpha"


@pytest.mark.asyncio
async def test_update_uses_mongo_id_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        return httpx.Response(
            200,
            json={"_id": "507f191e810c19729de860ea", "username": "alpha", "agent_facts_link": FACTS_URL + "?v=2"},
        )

    client = _client(handler)
    result = await client.update(
        "507f191e810c19729de860ea",
        agent_facts_link=FACTS_URL + "?v=2",
    )
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/agents/507f191e810c19729de860ea"
    assert result.agent_facts_link.endswith("?v=2")


@pytest.mark.asyncio
async def test_deregister_idempotent_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    await client.deregister("507f191e810c19729de860ea")  # should not raise


@pytest.mark.asyncio
async def test_deregister_hits_mongo_id_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    client = _client(handler)
    await client.deregister("507f191e810c19729de860ea")
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/api/agents/507f191e810c19729de860ea"


# ---------------------------------------------------------------- Auth + errors


@pytest.mark.asyncio
async def test_session_cookie_sent():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json=[])

    client = _client(handler, cookie="connect.sid=s%3Asomecookie")
    await client.list_agents()
    assert captured["cookie"] == "connect.sid=s%3Asomecookie"


@pytest.mark.asyncio
async def test_bearer_token_sent():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=[])

    client = _client(handler, token="secret")
    await client.list_agents()
    assert captured["auth"] == "Bearer secret"


@pytest.mark.asyncio
async def test_401_raises_unauthorized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "login required"})

    client = _client(handler)
    with pytest.raises(NandaIndexUnauthorized):
        await client.register(username="alpha", agent_facts_link=FACTS_URL)


@pytest.mark.asyncio
async def test_5xx_retries_then_unavailable():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(503)

    client = _client(handler)
    with pytest.raises(NandaIndexUnavailable):
        await client.list_agents()
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_404_on_update_raises_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    with pytest.raises(NandaIndexNotFound):
        await client.update("507f191e810c19729de860ea", agent_facts_link=FACTS_URL)


@pytest.mark.asyncio
async def test_non_array_list_response_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "an array"})

    client = _client(handler)
    with pytest.raises(NandaIndexInvalidResponse):
        await client.list_agents()
