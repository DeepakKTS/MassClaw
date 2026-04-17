from __future__ import annotations

goal = (
    "For the subject 'deadline', call MassClaw's fact-resolution endpoint three times — "
    "once with mode 'planning', once with mode 'audit', once with mode 'sensitive'. "
    "Report how the three responses differ: does one give a single chosen winner? "
    "Does one give a list? Does one flag the conflict for human review?"
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    resolve_calls = [
        c for c in http_calls
        if "/api/v1/memory/facts/resolve" in c["url"] and c["method"].upper() == "POST"
    ]
    if len(resolve_calls) < 3:
        return False, "next_steps_missing", f"subagent only called resolve {len(resolve_calls)} times; need 3"
    if not all(c.get("status") == 200 for c in resolve_calls):
        return False, "http_500", "at least one resolve call did not return 200"
    narrative_lower = (narrative or "").lower()
    for mode in ("planning", "audit", "sensitive"):
        if mode not in narrative_lower:
            return False, "transcript_stuck", f"subagent narrative does not mention mode '{mode}'"
    return True, None, "three-mode resolve surfaced"
