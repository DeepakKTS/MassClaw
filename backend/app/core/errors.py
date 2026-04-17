"""Standardized error envelope for every API response.

The envelope shape is the same across HTTPException, Pydantic validation
errors, :class:`MassClawError` subclasses, and uncaught exceptions:

.. code-block:: json

    {
      "error_code": "VALIDATION_ERROR",
      "detail": "human-readable explanation",
      "next_steps": "what the caller should do next",
      "correlation_id": "…"
    }

Why ``next_steps``: stock agents (OpenClaw, judges' harnesses) need a
hint they can paste back into their loop. "not found" is less useful
than "verify the workflow_id exists via GET /workflows". The field is
opt-in — when we have a specific hint we surface it; otherwise it's
absent rather than filler.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Mapping from error_code → canned next_steps hint. Per-endpoint code
# can override by returning its own envelope.
NEXT_STEPS_HINTS: dict[str, str] = {
    "VALIDATION_ERROR": "Inspect the `errors` field to fix the malformed input, then retry.",
    "NOT_FOUND": "Confirm the resource ID exists; list endpoints via GET can help locate it.",
    "CONFLICT": "Reload the current state (GET the resource) and re-submit with the latest version.",
    "AUTHENTICATION_FAILED": "Provide valid credentials or an API key and retry.",
    "FORBIDDEN": "This action requires elevated permissions — contact the workspace admin.",
    "RATE_LIMITED": "Back off for the seconds in the Retry-After header, then retry.",
    "BUDGET_EXHAUSTED": "Raise the workflow's budget_limit or split the job into smaller chunks.",
    "AGENT_UNAVAILABLE": "No agent currently matches this capability — retry later or broaden the request.",
    "POLICY_VIOLATION": "The action was blocked by an active policy rule — see the `rule_name` metadata.",
    "CIRCUIT_BREAKER_OPEN": "A downstream service is degraded — retry in ~30 seconds.",
    "DAG_VALIDATION_ERROR": "The submitted task graph has a cycle or missing dependency — fix it and retry.",
    "TOOL_EXECUTION_ERROR": "The tool call failed — check the tool is registered and the input is valid.",
    "PAYLOAD_TOO_LARGE": "Request body exceeds the server limit — split the payload and retry.",
    "INTERNAL_ERROR": "An unexpected error occurred — retry once; if it persists, report the correlation_id.",
}


class ErrorEnvelope(BaseModel):
    """Uniform shape every error response follows."""

    error_code: str = Field(..., description="Stable machine-readable code.")
    detail: str = Field(..., description="Human-readable explanation of what went wrong.")
    next_steps: str | None = Field(
        default=None,
        description="Hint for weak/stock agents on what to do next.",
    )
    correlation_id: str | None = Field(
        default=None,
        description="X-Correlation-ID header value — paste into bug reports.",
    )
    errors: list[dict[str, Any]] | None = Field(
        default=None,
        description="Structured per-field errors (for VALIDATION_ERROR).",
    )
    extra: dict[str, Any] | None = Field(
        default=None,
        description="Additional context — rule_name, retry_after, etc.",
    )


def next_steps_for(error_code: str) -> str | None:
    """Return the canned hint for a known code, or ``None``."""
    return NEXT_STEPS_HINTS.get(error_code)


def envelope(
    *,
    error_code: str,
    detail: str,
    correlation_id: str | None = None,
    next_steps: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an error-envelope dict — the single place we construct one.

    Callers pass whichever of the optional fields apply; missing
    fields are dropped from the output so the payload stays tight.
    """
    data: dict[str, Any] = {"error_code": error_code, "detail": detail}
    steps = next_steps if next_steps is not None else next_steps_for(error_code)
    if steps:
        data["next_steps"] = steps
    if correlation_id:
        data["correlation_id"] = correlation_id
    if errors:
        data["errors"] = errors
    if extra:
        data["extra"] = extra
    return data
