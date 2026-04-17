from __future__ import annotations

goal = (
    "Submit a workflow to MassClaw with a budget cap of 100 credits (any plausible task, e.g., "
    "'tell me a one-sentence fact about octopuses'). "
    "Wait for completion. "
    "Then fetch the wallet ledger for that workflow and report what event types you saw "
    "(for example reserve, charge, release, credit)."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    submitted = [
        c for c in http_calls
        if "/api/v1/workflows/submit" in c["url"] and c["method"].upper() == "POST" and c.get("status") in (200, 201, 202)
    ]
    if not submitted:
        return False, "next_steps_missing", "subagent never submitted a workflow"
    events_calls = [
        c for c in http_calls
        if "/api/v1/wallet/workflow/" in c["url"] and c["url"].endswith("/events")
    ]
    if not events_calls:
        return False, "budget_not_reserved", "subagent never fetched the wallet events ledger"
    if not any(c.get("status") == 200 for c in events_calls):
        return False, "http_500", "wallet/events did not return 200"
    narrative_lower = (narrative or "").lower()
    event_words = ("reserve", "charge", "release", "credit", "debit")
    if not any(w in narrative_lower for w in event_words):
        return False, "transcript_stuck", "subagent did not mention any wallet event types"
    return True, None, "wallet budget observed"
