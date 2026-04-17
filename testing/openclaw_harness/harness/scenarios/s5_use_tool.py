from __future__ import annotations

goal = (
    "Ask MassClaw to search the web for 'MIT Media Lab' and give you a one-sentence summary of "
    "what MIT Media Lab is. Submit the task as a workflow, wait for it to finish, "
    "and report the summary."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    submitted = [
        c for c in http_calls
        if "/api/v1/workflows/submit" in c["url"] and c["method"].upper() == "POST"
    ]
    if not submitted:
        return False, "next_steps_missing", "subagent never submitted a workflow"
    if not any(c.get("status") in (200, 201, 202) for c in submitted):
        return False, "http_500", "workflow submit did not return success"
    result_calls = [c for c in http_calls if "/workflows/" in c["url"] and "/result" in c["url"] and c.get("status") == 200]
    narrative_lower = (narrative or "").lower()
    if not result_calls and "completed" not in narrative_lower and "failed" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not reach a terminal workflow state"
    if "mit" not in narrative_lower or "media lab" not in narrative_lower:
        return False, "tool_not_triggered", "subagent narrative does not reference MIT Media Lab content"
    return True, None, "tool-using workflow succeeded"
