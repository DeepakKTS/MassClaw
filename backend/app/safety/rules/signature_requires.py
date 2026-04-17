"""Signature-requires rule: memory writes must carry a presented signature.

:mod:`memory_write_attribution` checks that the caller *identifies*
themselves with a DID. This rule enforces that the action actually
carries a signature blob. In production both are required: without
DID + signature together, anyone could forge a record under someone
else's DID.

Signature *verification* happens in the CRDT store itself — this
rule only asserts that the scheduler was handed a signature in the
first place, so we fail fast before hitting the store.
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "signature_requires"


def _is_memory_write(ctx: PolicyContext) -> bool:
    return ctx.action == "write_memory" or ctx.action_category == "memory_write"


@policy_rule(
    rule_id=RULE_ID,
    description="Memory writes must carry a signature blob in the action context.",
    priority=26,  # Right after memory_write_attribution so both can fire.
    tags=("memory", "signature", "provenance"),
)
async def signature_requires(ctx: PolicyContext) -> Decision:
    if not _is_memory_write(ctx):
        return Decision.abstain(rule_id=RULE_ID)

    signature = ctx.extra.get("signature")
    if not isinstance(signature, str) or len(signature.strip()) == 0:
        return Decision.deny(
            rule_id=RULE_ID,
            reason="memory write action has no signature payload",
            metadata={"signature_present": False},
        )

    return Decision.allow(
        rule_id=RULE_ID,
        reason="signature payload present",
        metadata={"signature_present": True, "signature_length": len(signature)},
    )
