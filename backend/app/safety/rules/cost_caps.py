"""Cost-cap rule: escalate or deny based on estimated_cost.

Splits the cost band into three zones so the scheduler can route the
task sensibly without a human-in-the-loop on every modest LLM call:

- ``< ESCALATE_AT`` → allow silently.
- ``ESCALATE_AT ≤ cost < DENY_AT`` → escalate_human (a reviewer OKs).
- ``>= DENY_AT`` → deny outright; caller has to break the work into
  smaller chunks or request a larger budget via another surface.

The two thresholds live in ``ctx.extra`` so hot-reloaded policy
configuration can override them per workflow without a code change.
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "cost_caps"
DEFAULT_ESCALATE_AT = 10.0
DEFAULT_DENY_AT = 50.0


@policy_rule(
    rule_id=RULE_ID,
    description="Escalate or deny tasks whose estimated cost crosses configured thresholds.",
    priority=30,
    tags=("cost", "budget"),
)
async def cost_caps(ctx: PolicyContext) -> Decision:
    cost = ctx.estimated_cost
    if cost is None:
        return Decision.abstain(rule_id=RULE_ID)

    escalate_at = float(ctx.extra.get("cost_escalate_at", DEFAULT_ESCALATE_AT))
    deny_at = float(ctx.extra.get("cost_deny_at", DEFAULT_DENY_AT))

    if cost >= deny_at:
        return Decision.deny(
            rule_id=RULE_ID,
            reason=f"estimated cost {cost} >= hard cap {deny_at}",
            metadata={"estimated_cost": cost, "deny_at": deny_at},
        )
    if cost >= escalate_at:
        return Decision.escalate_human(
            rule_id=RULE_ID,
            reason=f"estimated cost {cost} crossed escalation threshold {escalate_at}",
            metadata={"estimated_cost": cost, "escalate_at": escalate_at},
        )
    return Decision.allow(
        rule_id=RULE_ID,
        reason=f"estimated cost {cost} within free-flow band",
        metadata={"estimated_cost": cost},
    )
