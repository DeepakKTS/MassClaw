from __future__ import annotations

goal = (
    "Fetch MassClaw's AgentFacts document and then cryptographically prove it is genuine. "
    "You must use the verify-facts endpoint. "
    "Report whether the document's signature was valid."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    fetched = any("/.well-known/agent-facts.json" in c["url"] and c.get("status") == 200 for c in http_calls)
    if not fetched:
        return False, "transcript_stuck", "subagent never fetched the agent-facts doc"
    verified_calls = [c for c in http_calls if "/api/v1/agents/verify-facts" in c["url"] and c["method"].upper() == "POST"]
    if not verified_calls:
        return False, "next_steps_missing", "subagent never called /api/v1/agents/verify-facts"
    if not any(c.get("status") == 200 for c in verified_calls):
        return False, "signature_invalid", "verify-facts endpoint did not return 200"
    narrative_lower = (narrative or "").lower()
    if "valid" not in narrative_lower and "verified" not in narrative_lower and "true" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not state the outcome of verification"
    return True, None, "identity verified"
