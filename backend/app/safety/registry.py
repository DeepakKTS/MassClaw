"""Registry-based policy engine — ``@policy_rule`` decorator, evaluator.

The Phase-1 design replaces the old JSON-condition policy engine with
a pure-Python rule model: each rule is an async function that takes a
:class:`PolicyContext` and returns a :class:`Decision`. The
:func:`policy_rule` decorator registers it into a process-global
:class:`PolicyRegistry` so enabling/disabling rules is just a matter
of toggling the registry entry.

Why this design:

- Rules become **diffable and reviewable** like any other code.
- Testing a rule is just calling a function with a fixture context.
- Complex rules (time-of-day windows, cross-agent delegation graphs,
  memory-backed recency checks) are expressed directly in Python,
  not squeezed into a JSON DSL.
- The legacy JSON engine in :mod:`app.safety.policy_engine` stays put;
  the two systems coexist until Day 17's frontend swap is complete.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from app.core.logging import get_logger
from app.safety.context import PolicyContext
from app.safety.decision import Decision, DecisionAction, aggregate

logger = get_logger(__name__)


RuleFunc = Callable[[PolicyContext], Awaitable[Decision]]


@dataclass
class PolicyRuleEntry:
    """A single registered rule."""

    rule_id: str
    description: str
    function: RuleFunc
    priority: int = 0  # lower runs first; matters only when a rule depends on another's side effect.
    enabled: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)


class PolicyRegistry:
    """Process-global registry of :class:`PolicyRuleEntry` instances.

    Not thread-safe for concurrent writes — rules are normally
    registered at import time via the :func:`policy_rule` decorator
    and only mutated at runtime through the ``/policy`` API, which the
    frontend serialises.
    """

    _rules: dict[str, PolicyRuleEntry] = {}

    # ------------------------------------------------------------------
    # Registration / lookup
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, entry: PolicyRuleEntry) -> None:
        """Insert or replace ``entry`` in the registry."""
        if entry.rule_id in cls._rules:
            logger.info("policy_rule_replaced", rule_id=entry.rule_id)
        cls._rules[entry.rule_id] = entry

    @classmethod
    def unregister(cls, rule_id: str) -> bool:
        return cls._rules.pop(rule_id, None) is not None

    @classmethod
    def get(cls, rule_id: str) -> PolicyRuleEntry | None:
        return cls._rules.get(rule_id)

    @classmethod
    def all(cls, *, include_disabled: bool = False) -> list[PolicyRuleEntry]:
        """Return rules in ``(priority asc, rule_id asc)`` order."""
        rules = [r for r in cls._rules.values() if include_disabled or r.enabled]
        return sorted(rules, key=lambda r: (r.priority, r.rule_id))

    @classmethod
    def clear(cls) -> None:
        """Wipe the registry. Tests call this to isolate fixtures."""
        cls._rules.clear()

    @classmethod
    def set_enabled(cls, rule_id: str, enabled: bool) -> bool:
        entry = cls._rules.get(rule_id)
        if entry is None:
            return False
        entry.enabled = enabled
        logger.info("policy_rule_toggled", rule_id=rule_id, enabled=enabled)
        return True


def policy_rule(
    *,
    rule_id: str,
    description: str = "",
    priority: int = 0,
    tags: tuple[str, ...] = (),
    enabled: bool = True,
) -> Callable[[RuleFunc], RuleFunc]:
    """Decorator that registers a policy rule into :class:`PolicyRegistry`.

    The decorated function must be ``async def`` and accept a single
    :class:`PolicyContext`. Non-async functions are wrapped transparently
    so rules that don't need to ``await`` anything stay readable.
    """

    def _decorator(fn: Callable[..., Any]) -> RuleFunc:
        async_fn: RuleFunc
        if inspect.iscoroutinefunction(fn):
            async_fn = cast(RuleFunc, fn)
        else:

            async def _sync_wrapper(ctx: PolicyContext) -> Decision:
                return fn(ctx)

            _sync_wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
            async_fn = _sync_wrapper

        entry = PolicyRuleEntry(
            rule_id=rule_id,
            description=description,
            function=async_fn,
            priority=priority,
            tags=tuple(tags),
            enabled=enabled,
        )
        PolicyRegistry.register(entry)
        return async_fn

    return _decorator


# ---------------------------------------------------------------------------
# Evaluation entry point
# ---------------------------------------------------------------------------


async def evaluate(
    ctx: PolicyContext,
    *,
    rule_ids: list[str] | None = None,
) -> Decision:
    """Run the registered rules against ``ctx`` and aggregate.

    Parameters
    ----------
    ctx:
        The policy context to evaluate.
    rule_ids:
        If provided, only these specific rules are evaluated (handy
        for the dry-run API and targeted tests). ``None`` means run
        every enabled rule.

    Returns
    -------
    Decision
        The aggregated verdict. See :func:`app.safety.decision.aggregate`
        for precedence.
    """
    if rule_ids is None:
        rules = PolicyRegistry.all()
    else:
        rules = [r for r in PolicyRegistry.all(include_disabled=True) if r.rule_id in set(rule_ids)]

    decisions: list[Decision] = []
    for rule in rules:
        try:
            verdict = await rule.function(ctx)
        except Exception as exc:
            # A crashing rule must not sink the whole evaluation. Log,
            # treat as abstain, and continue — rules are supposed to
            # be self-contained.
            logger.warning(
                "policy_rule_crashed",
                rule_id=rule.rule_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            decisions.append(Decision.abstain(rule_id=rule.rule_id, reason=f"rule crashed: {exc}"))
            continue
        if not isinstance(verdict, Decision):
            logger.warning(
                "policy_rule_returned_non_decision",
                rule_id=rule.rule_id,
                type=type(verdict).__name__,
            )
            decisions.append(Decision.abstain(rule_id=rule.rule_id, reason="rule returned non-Decision"))
            continue
        decisions.append(verdict)
        # Short-circuit on a hard deny — every remaining rule could
        # only add noise, and hot paths should stay fast.
        if verdict.action == DecisionAction.DENY:
            break

    return aggregate(decisions)
