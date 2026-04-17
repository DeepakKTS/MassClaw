"""PolicyContext — the bundle of state that every policy rule sees.

Rules are pure Python functions that inspect a :class:`PolicyContext`
and return a :class:`Decision`. Keeping the context as a frozen
dataclass makes rules trivially testable (build a context, call the
rule) and keeps the contract stable as we evolve the rule set.

Design notes:

- Async query handles (``session``, ``redis``) are exposed so a rule
  that needs to peek into history (e.g. "how many times has this
  agent invoked this tool in the last minute?") can do so without
  being passed extra helpers.
- ``extra`` exists for rule-specific payload that doesn't belong in
  the canonical fields — e.g. a rule inspecting a specific request
  body may need the body itself. Callers populate it as needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PolicyContext:
    """Inputs available to every policy rule when it evaluates an action."""

    # ---- Agent / caller identity ----------------------------------
    agent_did: str | None = None
    agent_trust_score: float | None = None
    agent_capabilities: tuple[str, ...] = ()
    agent_id: uuid.UUID | None = None

    # ---- Action under evaluation ----------------------------------
    action: str = ""
    action_category: str | None = None
    tool_name: str | None = None
    amount: float | None = None
    target: str | None = None
    estimated_cost: float | None = None

    # ---- Workflow + timing ----------------------------------------
    workflow_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ---- Async query handles (optional; rules use what they need) -
    session: AsyncSession | None = None
    redis: aioredis.Redis | None = None

    # ---- Escape hatch ---------------------------------------------
    extra: dict[str, Any] = field(default_factory=dict)

    def with_extra(self, **kwargs: Any) -> PolicyContext:
        """Return a copy with ``extra`` merged — handy in tests."""
        merged = {**self.extra, **kwargs}
        return dataclasses_replace(self, extra=merged)


def dataclasses_replace(ctx: PolicyContext, **changes: Any) -> PolicyContext:
    """Thin wrapper over :func:`dataclasses.replace` kept here so callers
    don't need to import both dataclasses and the context module.
    """
    from dataclasses import replace

    return replace(ctx, **changes)
