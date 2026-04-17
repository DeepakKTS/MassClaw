"""Trust-floor rule: payments require a minimum agent trust score.

Semantics
- Trigger: ``action_category == "payment"`` (or ``action`` starts with
  ``"pay_"``) AND ``amount`` is a positive number.
- Decisions:
    * ``trust_score >= 0.6`` → allow.
    * ``0.4 <= trust_score < 0.6`` → escalate_human.
    * ``trust_score < 0.4`` or unknown → deny.

Rationale: payments are the highest-consequence action surface in
MassClaw. A low-trust agent should never move money without a human
explicitly approving, and we'd rather be overcautious than leak funds.
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "trust_floor_for_payments"
ESCALATE_FLOOR = 0.4
ALLOW_FLOOR = 0.6


def _is_payment(ctx: PolicyContext) -> bool:
    if ctx.action_category == "payment":
        return True
    if ctx.action and ctx.action.startswith("pay_"):
        return True
    return False


@policy_rule(
    rule_id=RULE_ID,
    description="Allow payments only when the agent's trust score clears the floor.",
    priority=10,
    tags=("payment", "trust"),
)
async def trust_floor_for_payments(ctx: PolicyContext) -> Decision:
    if not _is_payment(ctx):
        return Decision.abstain(rule_id=RULE_ID)
    if ctx.amount is None or ctx.amount <= 0:
        return Decision.abstain(rule_id=RULE_ID)

    score = ctx.agent_trust_score
    if score is None or score < ESCALATE_FLOOR:
        return Decision.deny(
            rule_id=RULE_ID,
            reason=f"agent trust score {score!r} below hard floor {ESCALATE_FLOOR}",
            metadata={"trust_score": score, "floor": ESCALATE_FLOOR, "amount": ctx.amount},
        )
    if score < ALLOW_FLOOR:
        return Decision.escalate_human(
            rule_id=RULE_ID,
            reason=f"agent trust score {score} in escalation band ({ESCALATE_FLOOR}–{ALLOW_FLOOR})",
            metadata={"trust_score": score, "amount": ctx.amount},
        )
    return Decision.allow(
        rule_id=RULE_ID,
        reason=f"agent trust score {score} meets allow floor {ALLOW_FLOOR}",
        metadata={"trust_score": score, "amount": ctx.amount},
    )
