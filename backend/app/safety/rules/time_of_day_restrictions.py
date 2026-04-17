"""Time-of-day rule: escalate sensitive actions outside business hours.

Sensitive action categories (configurable via ``ctx.extra`` or the
default set below) can only execute during the operator-defined
daytime window. Outside that window the rule escalates to a human so
there's always someone on the hook for after-hours mutations.

Defaults: 08:00 <= hour < 20:00 UTC, categories {"payment", "deploy",
"destructive"}.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from app.safety.context import PolicyContext
from app.safety.decision import Decision
from app.safety.registry import policy_rule

RULE_ID = "time_of_day_restrictions"
DEFAULT_START_HOUR = 8
DEFAULT_END_HOUR = 20
DEFAULT_SENSITIVE: frozenset[str] = frozenset({"payment", "deploy", "destructive"})


def _is_sensitive(category: str | None, sensitive: Iterable[str]) -> bool:
    if category is None:
        return False
    return category in set(sensitive)


@policy_rule(
    rule_id=RULE_ID,
    description="Escalate sensitive actions submitted outside business hours.",
    priority=40,
    tags=("time", "business_hours"),
)
async def time_of_day_restrictions(ctx: PolicyContext) -> Decision:
    sensitive_set = set(ctx.extra.get("sensitive_categories", DEFAULT_SENSITIVE))
    if not _is_sensitive(ctx.action_category, sensitive_set):
        return Decision.abstain(rule_id=RULE_ID)

    start_hour = int(ctx.extra.get("business_hours_start", DEFAULT_START_HOUR))
    end_hour = int(ctx.extra.get("business_hours_end", DEFAULT_END_HOUR))

    now = ctx.now if ctx.now.tzinfo else ctx.now.replace(tzinfo=UTC)
    hour_utc = now.astimezone(UTC).hour
    in_window = start_hour <= hour_utc < end_hour

    if in_window:
        return Decision.allow(
            rule_id=RULE_ID,
            reason=f"action {ctx.action_category!r} within business hours ({hour_utc:02d}:XX UTC)",
            metadata={"hour_utc": hour_utc, "window": [start_hour, end_hour]},
        )

    return Decision.escalate_human(
        rule_id=RULE_ID,
        reason=(
            f"action {ctx.action_category!r} submitted at {hour_utc:02d}:XX UTC — "
            f"outside business hours [{start_hour}:00, {end_hour}:00)"
        ),
        metadata={"hour_utc": hour_utc, "window": [start_hour, end_hour]},
    )


def _fmt_datetime(dt: datetime) -> str:
    """Small helper — kept to make future localisation easier."""
    return dt.astimezone(UTC).isoformat()
