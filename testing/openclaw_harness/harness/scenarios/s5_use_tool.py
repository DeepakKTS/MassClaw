from __future__ import annotations

goal = (
    "Ask MassClaw to use the web_search tool to find one fact about the MIT Media Lab "
    "and report it in a single sentence. Submit the workflow, poll /status until terminal, "
    "and fetch /result. Report the sentence you got back.\n\n"
    "Note: the test environment has Firecrawl configured as the web_search backend — "
    "real web data should come through. If the final content mentions MIT Media Lab with "
    "any real factual detail, the tool dispatch worked end-to-end."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    submitted = [
        c
        for c in http_calls
        if "/api/v1/workflows/submit" in c["url"] and c["method"].upper() == "POST"
    ]
    if not submitted:
        return False, "next_steps_missing", "subagent never submitted a workflow"
    if not any(c.get("status") in (200, 201, 202) for c in submitted):
        return False, "http_500", "workflow submit did not return success"
    result_calls = [
        c
        for c in http_calls
        if "/workflows/" in c["url"] and "/result" in c["url"] and c.get("status") == 200
    ]
    narrative_lower = (narrative or "").lower()
    if not result_calls and "completed" not in narrative_lower and "failed" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not reach a terminal workflow state"
    # The test: did the final narrative surface a real fact about MIT Media Lab?
    # We accept any of several factual anchors that a live web_search could
    # plausibly return — founding year, location, founders, research themes —
    # so the grader doesn't pin on one exact string that Firecrawl might phrase
    # differently day-to-day.
    factual_anchors = (
        "mit media lab",
        "media lab",
        "nicholas negroponte",
        "jerome wiesner",
        "cambridge",
        "massachusetts",
        "interdisciplinary",
        "antidisciplinary",
        "research lab",
        "founded in 1985",
        "1985",
    )
    hits = sum(1 for a in factual_anchors if a in narrative_lower)
    if hits < 2:
        return (
            False,
            "tool_not_triggered",
            f"narrative contains {hits}/2 factual anchors about MIT Media Lab",
        )
    return True, None, "tool-using workflow succeeded; web_search returned real data"
