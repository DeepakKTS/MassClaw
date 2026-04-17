from __future__ import annotations

goal = (
    "Your task is to discover what MassClaw can do. "
    "Find out its agent name, its version, at least two skills it offers, "
    "and which HTTP endpoint a client would use to submit a task. "
    "Report these four items back clearly."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    wk = [c for c in http_calls if "/.well-known/agent-facts.json" in c["url"]]
    if not wk:
        return False, "next_steps_missing", "subagent never fetched /.well-known/agent-facts.json"
    ok = any(c.get("status") == 200 for c in wk)
    if not ok:
        return False, "http_500", "agent-facts endpoint did not return 200"
    narrative_lower = (narrative or "").lower()
    if "skill" not in narrative_lower and "capability" not in narrative_lower:
        return False, "transcript_stuck", "subagent fetched the doc but did not extract skills/capabilities"
    if "/api/v1/workflows/submit" not in narrative and "workflows/submit" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not identify the workflow-submit endpoint"
    return True, None, "discover succeeded"
