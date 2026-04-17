"""Integration tests for the Day-17 /policy/registry/* endpoints.

Exercises the real FastAPI stack via ``httpx.AsyncClient + ASGITransport``
(same pattern as the identity tests) so routing, dependency wiring, and
the Pydantic surface all get covered end-to-end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import PolicyRegistry, policy_rule


@pytest_asyncio.fixture(autouse=True)
async def _clean_registry() -> AsyncIterator[None]:
    """Start every test with a known registry snapshot."""
    PolicyRegistry.clear()
    yield
    PolicyRegistry.clear()


@pytest_asyncio.fixture
async def client(db_session, redis_client) -> AsyncIterator[AsyncClient]:
    """Route the FastAPI app through the test's db_session + redis_client.

    Needed because the rate-limit middleware pulls a Redis client on
    every request — without these overrides the app exits 503 with
    "Redis manager not initialized".
    """
    from app.core.database import get_db_session
    from app.core.redis import get_redis

    async def _db_override() -> AsyncIterator:
        yield db_session

    async def _redis_override() -> AsyncIterator:
        yield redis_client

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_redis] = _redis_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


class TestListRules:
    @pytest.mark.asyncio
    async def test_empty_registry_returns_empty(self, client) -> None:
        resp = await client.get("/api/v1/policy/registry/rules")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_registered_rules_appear(self, client) -> None:
        @policy_rule(rule_id="t_one", description="first rule", priority=5, tags=("a",))
        async def one(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="t_one")

        @policy_rule(rule_id="t_two", description="second rule", priority=10)
        async def two(ctx: PolicyContext) -> Decision:
            return Decision.deny(rule_id="t_two", reason="nope")

        resp = await client.get("/api/v1/policy/registry/rules")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # Priority-sorted.
        assert body[0]["rule_id"] == "t_one"
        assert body[0]["tags"] == ["a"]
        assert body[1]["rule_id"] == "t_two"
        assert body[1]["enabled"] is True


class TestGetRule:
    @pytest.mark.asyncio
    async def test_unknown_rule_is_404(self, client) -> None:
        resp = await client.get("/api/v1/policy/registry/rules/does_not_exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_known_rule_returns_details(self, client) -> None:
        @policy_rule(rule_id="t_known", description="rule under test", priority=1, tags=("x", "y"))
        async def known(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="t_known")

        resp = await client.get("/api/v1/policy/registry/rules/t_known")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rule_id"] == "t_known"
        assert set(body["tags"]) == {"x", "y"}


class TestToggleRule:
    @pytest.mark.asyncio
    async def test_disable_then_enable_cycles_state(self, client) -> None:
        @policy_rule(rule_id="t_toggle")
        async def rule(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="t_toggle")

        r1 = await client.post("/api/v1/policy/registry/rules/t_toggle/disable")
        assert r1.status_code == 200
        assert r1.json()["enabled"] is False
        assert PolicyRegistry.get("t_toggle").enabled is False

        r2 = await client.post("/api/v1/policy/registry/rules/t_toggle/enable")
        assert r2.status_code == 200
        assert r2.json()["enabled"] is True
        assert PolicyRegistry.get("t_toggle").enabled is True

    @pytest.mark.asyncio
    async def test_toggle_unknown_is_404(self, client) -> None:
        r = await client.post("/api/v1/policy/registry/rules/ghost/enable")
        assert r.status_code == 404


class TestEvaluateDryRun:
    @pytest.mark.asyncio
    async def test_deny_rule_reflected_in_response(self, client) -> None:
        @policy_rule(rule_id="t_deny_all")
        async def deny_all(ctx: PolicyContext) -> Decision:
            return Decision.deny(rule_id="t_deny_all", reason="dry-run deny")

        resp = await client.post(
            "/api/v1/policy/registry/evaluate",
            json={"action": "execute_tool", "tool_name": "web_search"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "deny"
        assert body["rule_id"] == "t_deny_all"
        assert body["reason"] == "dry-run deny"

    @pytest.mark.asyncio
    async def test_rule_ids_filter_bypasses_other_rules(self, client) -> None:
        @policy_rule(rule_id="t_denier", priority=1)
        async def denier(ctx: PolicyContext) -> Decision:
            return Decision.deny(rule_id="t_denier", reason="block")

        @policy_rule(rule_id="t_allower", priority=2)
        async def allower(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="t_allower")

        # Without filter → deny wins.
        r1 = await client.post("/api/v1/policy/registry/evaluate", json={"action": "x"})
        assert r1.json()["action"] == "deny"

        # With filter to only t_allower → allow.
        r2 = await client.post(
            "/api/v1/policy/registry/evaluate",
            json={"action": "x", "rule_ids": ["t_allower"]},
        )
        assert r2.json()["action"] == "allow"

    @pytest.mark.asyncio
    async def test_empty_registry_evaluation_abstains(self, client) -> None:
        resp = await client.post("/api/v1/policy/registry/evaluate", json={"action": "x"})
        assert resp.status_code == 200
        assert resp.json()["action"] == "abstain"
