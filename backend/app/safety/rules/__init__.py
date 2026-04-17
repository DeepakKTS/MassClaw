"""Built-in policy rules.

Each rule lives in its own module and self-registers via
:func:`app.safety.registry.policy_rule` when imported. Importing this
package triggers every rule import below, so after ``import
app.safety.rules`` the :class:`app.safety.registry.PolicyRegistry` is
fully populated with the Phase-1 defaults.

The explicit import list doubles as a kill switch: comment a line out
and that rule stops being auto-loaded, without needing a runtime
disable flag.
"""

from __future__ import annotations

# Side-effect imports — the decorator call at module-import time does
# all the registration work. The ``noqa: F401`` markers stop lint from
# deleting them, and the alphabetical order keeps rule registration
# deterministic.
from app.safety.rules import cost_caps as _cost_caps  # noqa: F401
from app.safety.rules import credential_requires as _credential_requires  # noqa: F401
from app.safety.rules import cross_agent_delegation as _cross_agent_delegation  # noqa: F401
from app.safety.rules import default_deny_unknown_tool as _default_deny_unknown_tool  # noqa: F401
from app.safety.rules import memory_write_attribution as _memory_write_attribution  # noqa: F401
from app.safety.rules import pii_tool_guards as _pii_tool_guards  # noqa: F401
from app.safety.rules import rate_limit_per_tool as _rate_limit_per_tool  # noqa: F401
from app.safety.rules import signature_requires as _signature_requires  # noqa: F401
from app.safety.rules import time_of_day_restrictions as _time_of_day_restrictions  # noqa: F401
from app.safety.rules import trust_floor_for_payments as _trust_floor_for_payments  # noqa: F401

BUILTIN_RULE_IDS: tuple[str, ...] = (
    _cost_caps.RULE_ID,
    _credential_requires.RULE_ID,
    _cross_agent_delegation.RULE_ID,
    _default_deny_unknown_tool.RULE_ID,
    _memory_write_attribution.RULE_ID,
    _pii_tool_guards.RULE_ID,
    _rate_limit_per_tool.RULE_ID,
    _signature_requires.RULE_ID,
    _time_of_day_restrictions.RULE_ID,
    _trust_floor_for_payments.RULE_ID,
)


def load_builtin_rules() -> tuple[str, ...]:
    """Ensure every built-in rule is registered and return their IDs.

    Safe to call more than once — the registry replaces duplicate
    registrations idempotently. Intended to be called from
    :mod:`app.main` at app startup so the rules are live before the
    first request lands.
    """
    from app.safety.registry import PolicyRegistry

    loaded = tuple(r.rule_id for r in PolicyRegistry.all(include_disabled=True))
    return loaded


__all__ = ["BUILTIN_RULE_IDS", "load_builtin_rules"]
