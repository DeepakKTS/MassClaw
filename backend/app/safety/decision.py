"""Decision types for the :mod:`app.safety.registry` policy engine.

A rule returns a :class:`Decision` with one of four actions — allow,
deny, escalate_human, abstain — and :func:`aggregate` collapses a list
of per-rule decisions into the single outcome the scheduler acts on.

Aggregation rules (matches the Phase-1 design doc):

1. Any ``deny`` wins. If more than one rule denies, the first is
   canonical but the aggregated decision lists them all so audit
   output is complete.
2. Any ``escalate_human`` beats any ``allow`` (but loses to ``deny``).
3. Any ``allow`` wins over a pile of abstains.
4. If every rule abstains (or the registry is empty), the aggregate
   is ``abstain``. The scheduler interprets a bare ``abstain`` as
   ``allow`` at the call site — rules that don't speak up don't block.

Every aggregated decision is content-addressable: serialise it and
sign it with the node's instance key to produce an immutable audit
record. That hook lives in :meth:`Decision.to_audit_payload`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class DecisionAction(str, Enum):
    """The four terminal decisions a rule (or the aggregate) can return."""

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE_HUMAN = "escalate_human"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class Decision:
    """A single policy verdict.

    ``rule_id`` is the string key the rule was registered under. It is
    ``None`` for aggregate decisions that synthesise from several rules
    (then :attr:`contributing_rule_ids` is populated instead).
    """

    action: DecisionAction
    rule_id: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    contributing_rule_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Convenience constructors — keeps rule bodies readable.
    # ------------------------------------------------------------------

    @classmethod
    def allow(
        cls,
        *,
        rule_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Decision:
        return cls(action=DecisionAction.ALLOW, rule_id=rule_id, reason=reason, metadata=dict(metadata or {}))

    @classmethod
    def deny(
        cls,
        *,
        rule_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Decision:
        return cls(action=DecisionAction.DENY, rule_id=rule_id, reason=reason, metadata=dict(metadata or {}))

    @classmethod
    def escalate_human(
        cls,
        *,
        rule_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Decision:
        return cls(
            action=DecisionAction.ESCALATE_HUMAN,
            rule_id=rule_id,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def abstain(
        cls,
        *,
        rule_id: str | None = None,
        reason: str = "",
    ) -> Decision:
        return cls(action=DecisionAction.ABSTAIN, rule_id=rule_id, reason=reason)

    # ------------------------------------------------------------------
    # Audit payload — shape fed to the signed memory record.
    # ------------------------------------------------------------------

    def to_audit_payload(self) -> dict[str, Any]:
        """Canonical dict form for signing + persisting as a memory record."""
        return {
            "action": self.action.value,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "metadata": dict(self.metadata),
            "contributing_rule_ids": list(self.contributing_rule_ids),
            "created_at": self.created_at.isoformat(),
        }


def aggregate(decisions: list[Decision]) -> Decision:
    """Fold per-rule decisions into a single outcome.

    Precedence: ``DENY`` > ``ESCALATE_HUMAN`` > ``ALLOW`` > ``ABSTAIN``.
    The returned decision's ``contributing_rule_ids`` lists every rule
    that produced a non-abstain vote, in registration order, so the
    audit trail is complete even when one rule "wins".
    """
    if not decisions:
        return Decision(action=DecisionAction.ABSTAIN, reason="no rules registered")

    non_abstain = [d for d in decisions if d.action != DecisionAction.ABSTAIN]
    if not non_abstain:
        return Decision(action=DecisionAction.ABSTAIN, reason="all rules abstained")

    denies = [d for d in non_abstain if d.action == DecisionAction.DENY]
    if denies:
        primary = denies[0]
        return Decision(
            action=DecisionAction.DENY,
            rule_id=primary.rule_id,
            reason=primary.reason,
            metadata=_merged_metadata(denies),
            contributing_rule_ids=tuple(d.rule_id for d in non_abstain if d.rule_id),
        )

    escalations = [d for d in non_abstain if d.action == DecisionAction.ESCALATE_HUMAN]
    if escalations:
        primary = escalations[0]
        return Decision(
            action=DecisionAction.ESCALATE_HUMAN,
            rule_id=primary.rule_id,
            reason=primary.reason,
            metadata=_merged_metadata(escalations),
            contributing_rule_ids=tuple(d.rule_id for d in non_abstain if d.rule_id),
        )

    # All remaining non-abstain decisions must be ALLOW at this point.
    allows = [d for d in non_abstain if d.action == DecisionAction.ALLOW]
    primary = allows[0]
    return Decision(
        action=DecisionAction.ALLOW,
        rule_id=primary.rule_id,
        reason=primary.reason,
        metadata=_merged_metadata(allows),
        contributing_rule_ids=tuple(d.rule_id for d in non_abstain if d.rule_id),
    )


def _merged_metadata(decisions: list[Decision]) -> dict[str, Any]:
    """Merge per-rule metadata into a single dict keyed by rule_id.

    Losing key collisions across rules would make audit forensics
    painful, so we namespace under the originating rule_id.
    """
    out: dict[str, Any] = {}
    for d in decisions:
        if not d.metadata:
            continue
        if d.rule_id:
            out.setdefault("by_rule", {})[d.rule_id] = d.metadata
        else:
            out.setdefault("unattributed", []).append(d.metadata)
    return out
