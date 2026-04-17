"""Credential-requires rule: sensitive actions need a fresh robustness credential.

This is the Phase-2 scaffolding hook for ZERIX integration: by Phase 2
each agent that wants to perform sensitive actions submits a signed
*robustness credential* to MassClaw. This rule denies sensitive
actions when no credential is present or the one we have is expired.

In Phase 1 the credential format is defined but most agents won't
carry one, so the rule only enforces if
``ctx.extra["require_credential"]`` is truthy. That lets operators
opt workflows in explicitly rather than breaking the default flow.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "credential_requires"

DEFAULT_SENSITIVE_CATEGORIES: frozenset[str] = frozenset({"payment", "deploy", "destructive"})


def _category_triggers(ctx: PolicyContext, sensitive: set[str]) -> bool:
    if ctx.action_category in sensitive:
        return True
    if bool(ctx.extra.get("require_credential")):
        return True
    return False


def _credential_is_valid(cred: dict | None, now: datetime) -> tuple[bool, str]:
    if not isinstance(cred, dict):
        return False, "credential missing"
    expires_at_raw = cred.get("expires_at")
    if not isinstance(expires_at_raw, str):
        return False, "credential has no expires_at"
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except ValueError:
        return False, f"credential.expires_at is unparseable: {expires_at_raw!r}"
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        return False, f"credential expired at {expires_at.isoformat()}"
    return True, "ok"


@policy_rule(
    rule_id=RULE_ID,
    description="Sensitive actions require a fresh, non-expired robustness credential.",
    priority=35,
    tags=("credential", "security"),
)
async def credential_requires(ctx: PolicyContext) -> Decision:
    sensitive_set = set(ctx.extra.get("sensitive_categories", DEFAULT_SENSITIVE_CATEGORIES))
    if not _category_triggers(ctx, sensitive_set):
        return Decision.abstain(rule_id=RULE_ID)

    cred = ctx.extra.get("credential")
    now = ctx.now if ctx.now.tzinfo else ctx.now.replace(tzinfo=UTC)
    ok, reason = _credential_is_valid(cred if isinstance(cred, dict) else None, now)
    if not ok:
        return Decision.deny(
            rule_id=RULE_ID,
            reason=f"sensitive action requires valid credential: {reason}",
            metadata={"category": ctx.action_category, "credential_issue": reason},
        )

    return Decision.allow(
        rule_id=RULE_ID,
        reason="valid credential presented",
        metadata={"credential_expires_at": cred.get("expires_at") if isinstance(cred, dict) else None},
    )
