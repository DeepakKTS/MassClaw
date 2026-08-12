"""LLM-backed reflection over task outputs.

The engine exposes a single :meth:`ReflectionEngine.reflect` that an
orchestrator calls after each task completes. It returns a structured
verdict (``accept | retry_task | add_verifier | re_plan | abort``) that
downstream scheduler hooks dispatch on.

Caching is opt-in: if you pass a Redis client, identical reflections
(same goal + same output content) short-circuit the LLM call. That
matters because reflection runs after *every* task and on retry the
output is often identical to the previous attempt — re-asking an LLM
"is this good?" on the same bytes wastes tokens without changing the
decision.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.llm.router import get_model_router

logger = get_logger(__name__)

REFLECTION_PROMPT = """You are a quality evaluator for an AI agent orchestration system.

## Original Goal
{goal}

## Completed Task Outputs So Far
{outputs}

## Question
Evaluate the outputs and decide the next action.

Respond with ONLY valid JSON:
{{
  "should_continue": true/false,
  "confidence": 0.0-1.0,
  "issues": ["list of issues found, if any"],
  "suggestions": ["list of improvements, if any"],
  "action": "accept|retry_task|add_verifier|re_plan|abort"
}}

Rules:
- "accept" if outputs are sufficient and high quality
- "retry_task" if a specific output is weak
- "add_verifier" if outputs need cross-checking
- "re_plan" if the approach needs fundamental change
- "abort" only if the goal is unachievable"""


# Reflection cache default TTL — long enough to catch retries and
# redundant calls within the same workflow, short enough that a behavior
# change on a re-executed task is actually re-evaluated.
REFLECTION_CACHE_TTL_SECONDS: int = 600
REFLECTION_CACHE_PREFIX: str = "reflection:cache"


class ReflectionResult(BaseModel):
    should_continue: bool = True
    confidence: float = 0.5
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    action: str = "accept"  # accept, retry_task, add_verifier, re_plan, abort


def _content_hash(goal_description: str, completed_outputs: list[dict[str, str]]) -> str:
    """Deterministic hash of the reflection inputs.

    Keyed on the goal + the concatenation of each output's capability
    and first 2000 chars of content. We bound content size because we
    only ever feed the first 2000 chars to the LLM anyway.
    """
    hasher = hashlib.sha256()
    hasher.update(goal_description.encode("utf-8"))
    hasher.update(b"\x00")
    for output in completed_outputs:
        capability = str(output.get("capability", ""))
        content = str(output.get("content", ""))[:2000]
        hasher.update(capability.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _cache_key(goal_description: str, completed_outputs: list[dict[str, str]]) -> str:
    """Redis key for a reflection cache entry."""
    return f"{REFLECTION_CACHE_PREFIX}:{_content_hash(goal_description, completed_outputs)}"


class ReflectionEngine:
    """Evaluate intermediate results and decide next action.

    Parameters
    ----------
    redis:
        Optional Redis client (``decode_responses=True``). When provided,
        successful reflections are cached for
        :data:`REFLECTION_CACHE_TTL_SECONDS` seconds keyed on a content
        hash of the inputs. When omitted the engine is stateless.
    cache_ttl_seconds:
        Override the default TTL (mostly for tests).
    """

    def __init__(
        self,
        redis: aioredis.Redis | None = None,
        *,
        cache_ttl_seconds: int = REFLECTION_CACHE_TTL_SECONDS,
    ) -> None:
        self.router = get_model_router()
        self._redis = redis
        self._cache_ttl = cache_ttl_seconds

    async def reflect(
        self,
        goal_description: str,
        completed_outputs: list[dict[str, str]],
    ) -> ReflectionResult:
        """Evaluate outputs against the goal and recommend next action."""
        if not completed_outputs:
            return ReflectionResult(
                should_continue=True,
                confidence=0.0,
                action="accept",
                issues=["No outputs to evaluate"],
            )

        cached = await self._cache_get(goal_description, completed_outputs)
        if cached is not None:
            return cached

        outputs_text = "\n\n".join(
            f"### {o.get('capability', 'unknown')}\n{o.get('content', '')[:1000]}" for o in completed_outputs
        )

        try:
            response = await self.router.generate(
                prompt=REFLECTION_PROMPT.format(goal=goal_description, outputs=outputs_text),
                model="claude-sonnet-5",
                max_tokens=300,
                temperature=0.1,
            )

            content = response.content.strip()
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(content[start : end + 1])
                result = ReflectionResult(
                    should_continue=parsed.get("should_continue", True),
                    confidence=parsed.get("confidence", 0.5),
                    issues=parsed.get("issues", []),
                    suggestions=parsed.get("suggestions", []),
                    action=parsed.get("action", "accept"),
                )
                logger.info(
                    "reflection_complete",
                    action=result.action,
                    confidence=result.confidence,
                    issues_count=len(result.issues),
                )
                await self._cache_put(goal_description, completed_outputs, result)
                return result

        except Exception as e:
            logger.warning("reflection_failed", error=str(e))

        return ReflectionResult(
            should_continue=False,
            confidence=0.7,
            action="accept",
            issues=[],
            suggestions=["Reflection LLM call failed, accepting outputs"],
        )

    # ------------------------------------------------------------------
    # Cache plumbing (Redis-backed, best-effort)
    # ------------------------------------------------------------------

    async def _cache_get(
        self,
        goal_description: str,
        completed_outputs: list[dict[str, str]],
    ) -> ReflectionResult | None:
        if self._redis is None:
            return None
        try:
            raw: str | None = await self._redis.get(_cache_key(goal_description, completed_outputs))
        except Exception as exc:
            logger.warning("reflection_cache_read_failed", error=str(exc))
            return None
        if not raw:
            return None
        try:
            payload: dict[str, Any] = json.loads(raw)
            result = ReflectionResult.model_validate(payload)
        except Exception as exc:
            logger.warning("reflection_cache_decode_failed", error=str(exc))
            return None
        logger.info(
            "reflection_cache_hit",
            action=result.action,
            confidence=result.confidence,
        )
        return result

    async def _cache_put(
        self,
        goal_description: str,
        completed_outputs: list[dict[str, str]],
        result: ReflectionResult,
    ) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _cache_key(goal_description, completed_outputs),
                result.model_dump_json(),
                ex=self._cache_ttl,
            )
        except Exception as exc:
            logger.warning("reflection_cache_write_failed", error=str(exc))
