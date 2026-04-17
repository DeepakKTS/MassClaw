"""Memory-write attribution: deny unsigned, anonymous writes to shared memory.

Every memory record in the CRDT store must have a verifiable author —
it's the whole point of signed provenance. This rule blocks writes
whose context either lacks an ``agent_did`` or carries one that
doesn't parse as a supported DID method (``did:key``, ``did:web``,
``did:nanda``).

This rule is separate from :mod:`signature_requires` so operators can
toggle *what* we enforce (presence of a DID vs. presence of a
signature blob) independently.
"""

from __future__ import annotations

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "memory_write_attribution"

SUPPORTED_DID_METHODS: tuple[str, ...] = ("did:key:", "did:web:", "did:nanda:")


def _is_memory_write(ctx: PolicyContext) -> bool:
    if ctx.action == "write_memory":
        return True
    if ctx.action_category == "memory_write":
        return True
    return False


def _looks_like_did(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in SUPPORTED_DID_METHODS)


@policy_rule(
    rule_id=RULE_ID,
    description="Block memory writes without a valid author DID.",
    priority=25,
    tags=("memory", "provenance"),
)
async def memory_write_attribution(ctx: PolicyContext) -> Decision:
    if not _is_memory_write(ctx):
        return Decision.abstain(rule_id=RULE_ID)

    did = ctx.agent_did
    if not did:
        return Decision.deny(
            rule_id=RULE_ID,
            reason="memory write attempted without an agent_did",
            metadata={"agent_did": None},
        )
    if not _looks_like_did(did):
        return Decision.deny(
            rule_id=RULE_ID,
            reason=f"agent_did {did!r} does not use a supported DID method",
            metadata={"agent_did": did, "supported": list(SUPPORTED_DID_METHODS)},
        )

    return Decision.allow(
        rule_id=RULE_ID,
        reason=f"memory write signed by {did}",
        metadata={"agent_did": did},
    )
