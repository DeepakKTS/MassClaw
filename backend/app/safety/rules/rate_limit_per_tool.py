"""Rate-limit rule: per-(agent_id, tool_name) sliding-window counter.

Uses a Redis sorted set (score = UNIX epoch ms) so we can prune stale
entries cheaply and count the current window without scanning every
key. Falls back to abstain if no Redis is configured — this rule is
defence-in-depth, not a hard requirement.

Defaults: 20 invocations / 60s / (agent, tool). Overrides via
``ctx.extra["rate_limit_capacity"]`` and ``ctx.extra["rate_limit_window_seconds"]``.
"""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

logger = get_logger(__name__)

RULE_ID = "rate_limit_per_tool"
DEFAULT_CAPACITY = 20
DEFAULT_WINDOW_SECONDS = 60
KEY_PREFIX = "policy:rate_limit"


def _key(agent_id: str, tool_name: str) -> str:
    return f"{KEY_PREFIX}:{agent_id}:{tool_name}"


@policy_rule(
    rule_id=RULE_ID,
    description="Deny tool calls that exceed a sliding-window per-(agent, tool) rate limit.",
    priority=20,
    tags=("rate_limit", "tool"),
)
async def rate_limit_per_tool(ctx: PolicyContext) -> Decision:
    if ctx.tool_name is None or ctx.agent_id is None or ctx.redis is None:
        return Decision.abstain(rule_id=RULE_ID)

    capacity = int(ctx.extra.get("rate_limit_capacity", DEFAULT_CAPACITY))
    window_s = int(ctx.extra.get("rate_limit_window_seconds", DEFAULT_WINDOW_SECONDS))
    if capacity <= 0 or window_s <= 0:
        return Decision.abstain(rule_id=RULE_ID)

    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - window_s * 1000
    key = _key(str(ctx.agent_id), ctx.tool_name)

    try:
        # Prune stale entries + count remaining + add a marker + set TTL.
        async with ctx.redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, window_start_ms)
            pipe.zcard(key)
            pipe.zadd(key, {f"{now_ms}-{ctx.task_id or 'na'}": now_ms})
            pipe.expire(key, window_s * 2)
            results = await pipe.execute()
    except Exception as exc:
        logger.warning("rate_limit_rule_redis_failed", error=str(exc))
        return Decision.abstain(rule_id=RULE_ID)

    count_before = int(results[1] or 0)
    # count_before is pre-insert; count_after = count_before + 1 (the one we just added).
    count_after = count_before + 1

    if count_after > capacity:
        return Decision.deny(
            rule_id=RULE_ID,
            reason=(
                f"agent {ctx.agent_id} exceeded {capacity} calls to {ctx.tool_name!r} "
                f"in {window_s}s window (observed {count_after})"
            ),
            metadata={
                "count_in_window": count_after,
                "capacity": capacity,
                "window_seconds": window_s,
            },
        )

    return Decision.allow(
        rule_id=RULE_ID,
        reason=f"{count_after}/{capacity} calls in {window_s}s window",
        metadata={"count_in_window": count_after, "capacity": capacity},
    )
