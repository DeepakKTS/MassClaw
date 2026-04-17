from __future__ import annotations

goal = (
    "Submit a workflow to MassClaw that triggers the human-approval gate. Use this "
    "exact instruction (short — decomposes into 2-3 tasks, not 10):\n\n"
    "  'Perform a compliance-check on the attached data retention policy. Output a "
    "  single paragraph stating whether the policy complies with GDPR.'\n\n"
    "Expect MassClaw to pause the workflow for human approval before executing the "
    "compliance-check step. Poll /status looking for 'awaiting_approval' (lowercase). "
    "Find the pending approval, capture BOTH its request_id AND checkpoint_hash. "
    "Approve the request. Resume the workflow with {checkpoint_hash, approval_id}. "
    "Poll /status until terminal.\n\n"
    "Do NOT submit destructive-action prompts like 'delete the database' — those trip "
    "MassClaw's safety reflection layer which aborts BEFORE the approval gate."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    submitted = [
        c for c in http_calls
        if "/api/v1/workflows/submit" in c["url"] and c["method"].upper() == "POST" and c.get("status") in (200, 201, 202)
    ]
    if not submitted:
        return False, "next_steps_missing", "subagent never submitted a workflow"
    pending = [c for c in http_calls if "/api/v1/approvals/pending" in c["url"]]
    if not pending:
        return False, "approval_state_missing", "subagent never queried /approvals/pending"
    approved = [c for c in http_calls if "/api/v1/approvals/" in c["url"] and c["url"].endswith("/approve") and c["method"].upper() == "POST"]
    resumed = [c for c in http_calls if "/api/v1/workflows/" in c["url"] and c["url"].endswith("/resume") and c["method"].upper() == "POST"]
    if not approved and not resumed:
        return False, "approval_state_missing", "subagent did not approve or resume the workflow"
    narrative_lower = (narrative or "").lower()
    if "awaiting_approval" not in narrative_lower and "approval" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not observe the awaiting-approval state"
    return True, None, "HITL path exercised"
