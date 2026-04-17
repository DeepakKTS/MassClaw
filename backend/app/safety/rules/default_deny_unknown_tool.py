"""Default-deny-unknown-tool: catch-all against tools not on the allowlist.

Runs at the lowest priority so explicit rules that *do* handle a tool
get first dibs. The allowlist lives in ``ctx.extra["known_tools"]``
so it can be rotated at runtime (via the `/policy` API) without
patching code; when absent, this rule abstains — the default deployment
doesn't enforce a global allowlist until operators turn it on.
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "default_deny_unknown_tool"


@policy_rule(
    rule_id=RULE_ID,
    description="Deny tool calls whose tool_name isn't on the operator-provided allowlist.",
    priority=999,  # runs last so targeted rules get the first word.
    tags=("tool", "allowlist", "defense-in-depth"),
)
async def default_deny_unknown_tool(ctx: PolicyContext) -> Decision:
    if ctx.tool_name is None:
        return Decision.abstain(rule_id=RULE_ID)

    known = ctx.extra.get("known_tools")
    if known is None:
        # No operator allowlist configured → rule is inert.
        return Decision.abstain(rule_id=RULE_ID)

    try:
        allowlist = set(known)
    except TypeError:
        return Decision.abstain(rule_id=RULE_ID)

    if ctx.tool_name in allowlist:
        return Decision.abstain(rule_id=RULE_ID)

    return Decision.deny(
        rule_id=RULE_ID,
        reason=f"tool {ctx.tool_name!r} is not on the known-tools allowlist",
        metadata={"tool_name": ctx.tool_name, "allowlist_size": len(allowlist)},
    )
