"""Pass/fail grading for a scenario run."""

from __future__ import annotations

from typing import Any

from .scenarios import REGISTRY


def grade_run(scenario: str, http_calls: list[dict[str, Any]], narrative: str) -> dict[str, Any]:
    """Return a verdict dict: {passed, failure_class, reason}."""
    entry = REGISTRY.get(scenario)
    if not entry:
        return {
            "passed": False,
            "failure_class": "transcript_stuck",
            "reason": f"unknown scenario '{scenario}'",
        }
    grade_fn = entry["grade"]
    passed, failure_class, reason = grade_fn(http_calls, narrative)  # type: ignore[operator]
    return {"passed": passed, "failure_class": failure_class, "reason": reason}
