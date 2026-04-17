"""Delegation-depth rule: escalate deep cross-agent delegation chains.

MassClaw agents can delegate tasks to other agents, and those in turn
can re-delegate. Past a certain depth the chain stops being
transparent to the operator: debugging gets expensive, and the
accountability graph fans out. This rule keeps chains short by
default and forces a human checkpoint when they grow.

Convention: callers populate ``ctx.extra["delegation_depth"]`` with
the current hop count (0 = originator, 1 = one delegation, …).
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "cross_agent_delegation"
DEFAULT_MAX_DEPTH = 2


def _is_delegation(ctx: PolicyContext) -> bool:
    if ctx.action == "delegate_task":
        return True
    if ctx.action_category == "delegation":
        return True
    return False


@policy_rule(
    rule_id=RULE_ID,
    description="Escalate delegation chains that exceed the configured depth.",
    priority=50,
    tags=("delegation", "accountability"),
)
async def cross_agent_delegation(ctx: PolicyContext) -> Decision:
    if not _is_delegation(ctx):
        return Decision.abstain(rule_id=RULE_ID)

    max_depth = int(ctx.extra.get("delegation_max_depth", DEFAULT_MAX_DEPTH))
    depth = int(ctx.extra.get("delegation_depth", 0))

    if depth > max_depth:
        return Decision.escalate_human(
            rule_id=RULE_ID,
            reason=f"delegation chain depth {depth} exceeds max {max_depth}",
            metadata={"delegation_depth": depth, "max_depth": max_depth},
        )

    return Decision.allow(
        rule_id=RULE_ID,
        reason=f"delegation chain depth {depth} within max {max_depth}",
        metadata={"delegation_depth": depth, "max_depth": max_depth},
    )
