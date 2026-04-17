"""PII-guard rule: escalate any action that touches personally identifiable data.

Triggers on either:
- ``tool_name`` appearing in the PII tool set, or
- ``action_category == "pii_access"``.

The response is always ``escalate_human`` — by policy, we don't
auto-approve PII access even for trusted agents. That keeps the audit
trail honest and gives the compliance side a chokepoint that is
independent of the agent's own guard-rails.
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "pii_tool_guards"

DEFAULT_PII_TOOLS: frozenset[str] = frozenset(
    {
        "lookup_ssn",
        "lookup_address",
        "lookup_phone",
        "lookup_email",
        "lookup_dob",
        "lookup_ip_geolocation",
        "export_user_data",
        "query_user_profile",
        "dump_contacts",
    }
)


@policy_rule(
    rule_id=RULE_ID,
    description="Route every PII tool invocation through a human reviewer.",
    priority=15,
    tags=("pii", "compliance"),
)
async def pii_tool_guards(ctx: PolicyContext) -> Decision:
    pii_tools = set(ctx.extra.get("pii_tools", DEFAULT_PII_TOOLS))
    tool_hits = ctx.tool_name is not None and ctx.tool_name in pii_tools
    category_hits = ctx.action_category == "pii_access"

    if not (tool_hits or category_hits):
        return Decision.abstain(rule_id=RULE_ID)

    return Decision.escalate_human(
        rule_id=RULE_ID,
        reason=(
            f"PII access via tool={ctx.tool_name!r}, category={ctx.action_category!r} always requires a human reviewer"
        ),
        metadata={
            "tool_name": ctx.tool_name,
            "action_category": ctx.action_category,
            "agent_did": ctx.agent_did,
        },
    )
