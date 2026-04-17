"""Unit tests for :class:`ReflectionEngine` caching.

The engine is exercised with a mock Redis + mock LLM router so the only
thing under test is the cache logic: key derivation, hit short-circuit,
miss falls through to the LLM, and cache write on success.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.intelligence.reflection import (
    REFLECTION_CACHE_PREFIX,
    ReflectionEngine,
    ReflectionResult,
    _cache_key,
    _content_hash,
)


class _InMemoryRedis:
    """Minimal Redis shim with the subset used by ReflectionEngine."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        self.set_calls.append((key, value, ex))
        return True


def _llm_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(content=json.dumps(payload))


@pytest.fixture
def patched_router(monkeypatch):
    """Patch get_model_router so each ReflectionEngine gets a mock router."""

    def _factory(*, response_payload, call_counter):
        router = SimpleNamespace(generate=AsyncMock(return_value=_llm_response(response_payload)))

        def _patched_get_model_router():
            call_counter["n"] += 1
            return router

        monkeypatch.setattr(
            "app.intelligence.reflection.get_model_router",
            _patched_get_model_router,
        )
        return router

    return _factory


class TestContentHashing:
    def test_hash_is_deterministic(self) -> None:
        outputs = [{"capability": "research", "content": "x"}]
        assert _content_hash("goal", outputs) == _content_hash("goal", outputs)

    def test_hash_changes_with_goal(self) -> None:
        outputs = [{"capability": "research", "content": "x"}]
        assert _content_hash("goal-a", outputs) != _content_hash("goal-b", outputs)

    def test_hash_changes_with_content(self) -> None:
        base = [{"capability": "research", "content": "x"}]
        alt = [{"capability": "research", "content": "y"}]
        assert _content_hash("goal", base) != _content_hash("goal", alt)

    def test_cache_key_has_known_prefix(self) -> None:
        key = _cache_key("goal", [{"capability": "research", "content": "x"}])
        assert key.startswith(REFLECTION_CACHE_PREFIX + ":")


class TestCacheBehaviour:
    @pytest.mark.asyncio
    async def test_miss_then_hit_only_calls_llm_once(self, patched_router) -> None:
        counter = {"n": 0}
        router = patched_router(
            response_payload={
                "should_continue": True,
                "confidence": 0.9,
                "action": "accept",
                "issues": [],
                "suggestions": [],
            },
            call_counter=counter,
        )
        redis = _InMemoryRedis()
        goal = "check the draft"
        outputs = [{"capability": "writing", "content": "draft v1"}]

        eng1 = ReflectionEngine(redis=redis)
        first = await eng1.reflect(goal, outputs)
        assert first.action == "accept"
        assert router.generate.await_count == 1

        # Fresh engine, same Redis — second call must be a cache hit.
        eng2 = ReflectionEngine(redis=redis)
        second = await eng2.reflect(goal, outputs)
        assert second.action == "accept"
        assert second.confidence == 0.9
        # Mock was reused across both engines (same monkeypatched factory)
        # but the second engine's LLM must not have been invoked.
        assert router.generate.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_write_uses_ttl(self, patched_router) -> None:
        counter = {"n": 0}
        patched_router(
            response_payload={
                "should_continue": False,
                "confidence": 0.2,
                "action": "retry_task",
                "issues": ["too short"],
                "suggestions": ["expand"],
            },
            call_counter=counter,
        )
        redis = _InMemoryRedis()
        engine = ReflectionEngine(redis=redis, cache_ttl_seconds=42)
        await engine.reflect("goal", [{"capability": "x", "content": "short"}])
        assert len(redis.set_calls) == 1
        key, payload, ex = redis.set_calls[0]
        assert ex == 42
        # Payload must round-trip into ReflectionResult.
        restored = ReflectionResult.model_validate_json(payload)
        assert restored.action == "retry_task"

    @pytest.mark.asyncio
    async def test_engine_without_redis_is_stateless(self, patched_router) -> None:
        counter = {"n": 0}
        router = patched_router(
            response_payload={
                "should_continue": True,
                "confidence": 0.8,
                "action": "accept",
                "issues": [],
                "suggestions": [],
            },
            call_counter=counter,
        )
        eng = ReflectionEngine(redis=None)
        goal = "goal"
        outputs = [{"capability": "x", "content": "y"}]
        await eng.reflect(goal, outputs)
        await eng.reflect(goal, outputs)
        # Both calls must reach the LLM.
        assert router.generate.await_count == 2

    @pytest.mark.asyncio
    async def test_different_outputs_break_cache(self, patched_router) -> None:
        counter = {"n": 0}
        router = patched_router(
            response_payload={
                "should_continue": True,
                "confidence": 0.8,
                "action": "accept",
                "issues": [],
                "suggestions": [],
            },
            call_counter=counter,
        )
        redis = _InMemoryRedis()
        eng = ReflectionEngine(redis=redis)
        await eng.reflect("goal", [{"capability": "x", "content": "v1"}])
        await eng.reflect("goal", [{"capability": "x", "content": "v2"}])
        assert router.generate.await_count == 2
        # Two distinct cache entries exist.
        assert len(redis._store) == 2

    @pytest.mark.asyncio
    async def test_empty_outputs_short_circuit_without_cache(self, patched_router) -> None:
        counter = {"n": 0}
        router = patched_router(
            response_payload={"should_continue": True, "confidence": 1.0, "action": "accept"},
            call_counter=counter,
        )
        redis = _InMemoryRedis()
        eng = ReflectionEngine(redis=redis)
        result = await eng.reflect("goal", [])
        assert result.action == "accept"
        assert result.confidence == 0.0  # the stub for "no outputs to evaluate"
        assert router.generate.await_count == 0
        assert redis._store == {}

    @pytest.mark.asyncio
    async def test_cache_read_failure_falls_back_to_llm(self, patched_router) -> None:
        counter = {"n": 0}
        router = patched_router(
            response_payload={"should_continue": True, "confidence": 0.8, "action": "accept"},
            call_counter=counter,
        )

        class _BrokenRedis:
            async def get(self, key: str) -> str | None:
                raise RuntimeError("redis is on fire")

            async def set(self, key: str, value: str, ex: int | None = None) -> bool:
                return True

        eng = ReflectionEngine(redis=_BrokenRedis())
        result = await eng.reflect("goal", [{"capability": "x", "content": "y"}])
        assert result.action == "accept"
        assert router.generate.await_count == 1
