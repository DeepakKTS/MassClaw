from __future__ import annotations

goal = (
    "Query MassClaw's shared memory for any facts about the word 'deadline'. "
    "Return the top 3 results ranked by relevance, or report clearly if no facts exist."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    query_calls = [
        c for c in http_calls
        if "/api/v1/memory/query" in c["url"] and c["method"].upper() == "POST"
    ]
    if not query_calls:
        return False, "next_steps_missing", "subagent never POSTed to /memory/query"
    ok = any(c.get("status") == 200 for c in query_calls)
    if not ok:
        return False, "http_500", "memory/query did not return 200"
    narrative_lower = (narrative or "").lower()
    if "deadline" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not report back on the deadline subject"
    if "no facts" not in narrative_lower and "empty" not in narrative_lower:
        if "relevance" not in narrative_lower and "similarity" not in narrative_lower:
            return False, "transcript_stuck", "subagent got results but did not mention ranking"
    return True, None, "memory queried"
