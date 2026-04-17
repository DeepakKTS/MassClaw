"""Built-in policy rules.

Each rule lives in its own module and self-registers via
:func:`app.safety.registry.policy_rule`. Importing this package is
enough to populate :class:`app.safety.registry.PolicyRegistry`.

Rule modules go here starting Day 16 — the ten Phase-1 rules
covering trust floors, rate limits, cost caps, PII guards, etc.
"""

from __future__ import annotations

# When rule modules land they'll be imported here so the registry
# is populated on package import. Keeping the list explicit makes
# disabling a rule a one-line comment-out.
#
#     from app.safety.rules import trust_floor_for_payments  # noqa: F401

__all__: list[str] = []
