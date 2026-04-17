from __future__ import annotations

goal = (
    "Ask MassClaw to sort the list [3, 1, 4, 1, 5, 9, 2, 6] in ascending order. "
    "Submit the task, wait for it to finish, and report the sorted result. "
    "Poll every 2 seconds. Give up after 60 seconds."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    submitted = [
        c for c in http_calls
        if "/api/v1/workflows/submit" in c["url"] and c["method"].upper() == "POST"
    ]
    if not submitted:
        return False, "next_steps_missing", "subagent never POSTed to /workflows/submit"
    if not any(c.get("status") in (200, 201, 202) for c in submitted):
        return False, "http_500", "submit did not return success"
    status_polls = [c for c in http_calls if "/workflows/" in c["url"] and "/status" in c["url"]]
    if len(status_polls) < 1:
        return False, "transcript_stuck", "subagent never polled /status"
    result_calls = [c for c in http_calls if "/workflows/" in c["url"] and "/result" in c["url"]]
    got_result = any(c.get("status") == 200 for c in result_calls)
    narrative_lower = (narrative or "").lower()
    terminal_mentioned = any(s in narrative_lower for s in ("completed", "failed", "cancelled"))
    if not (got_result or terminal_mentioned):
        return False, "transcript_stuck", "subagent never reached terminal state or fetched result"
    return True, None, "workflow submitted and tracked"
