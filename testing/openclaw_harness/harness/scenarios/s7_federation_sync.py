from __future__ import annotations

goal = (
    "MassClaw is running across three peer nodes: http://localhost:18001, "
    "http://localhost:18002, and http://localhost:18003. "
    "Confirm the three nodes are converged on the same memory state by fetching "
    "/api/v1/memory/sync/summary from each and comparing their 'root' fields. "
    "You may need peer-auth headers, but for convergence checking you can try the "
    "request without them first and see what the error envelope says."
)


def grade(http_calls: list[dict], narrative: str) -> tuple[bool, str | None, str]:
    summaries_per_node: dict[str, list[dict]] = {"18001": [], "18002": [], "18003": []}
    for c in http_calls:
        for port in ("18001", "18002", "18003"):
            if f":{port}" in c["url"] and "/memory/sync/summary" in c["url"]:
                summaries_per_node[port].append(c)
    hit_count = sum(1 for calls in summaries_per_node.values() if calls)
    if hit_count < 3:
        return False, "next_steps_missing", f"subagent only hit {hit_count}/3 nodes' /sync/summary"
    narrative_lower = (narrative or "").lower()
    if "root" not in narrative_lower and "merkle" not in narrative_lower and "match" not in narrative_lower and "converged" not in narrative_lower:
        return False, "transcript_stuck", "subagent did not report on root/convergence"
    return True, None, "federation convergence check performed"
