"""Scenario registry — s1 through s9."""

from __future__ import annotations

from typing import Callable

from . import (
    s1_discover,
    s2_verify_identity,
    s3_submit_workflow,
    s4_query_memory,
    s5_use_tool,
    s6_fact_resolve_modes,
    s7_federation_sync,
    s8_hitl_approval,
    s9_wallet_budget,
)

Scenario = tuple[str, Callable[..., tuple[bool, str | None, str]]]

REGISTRY: dict[str, dict[str, object]] = {
    "s1": {"label": "Discover", "goal": s1_discover.goal, "grade": s1_discover.grade},
    "s2": {
        "label": "Verify identity",
        "goal": s2_verify_identity.goal,
        "grade": s2_verify_identity.grade,
    },
    "s3": {
        "label": "Submit workflow",
        "goal": s3_submit_workflow.goal,
        "grade": s3_submit_workflow.grade,
    },
    "s4": {
        "label": "Query memory",
        "goal": s4_query_memory.goal,
        "grade": s4_query_memory.grade,
    },
    "s5": {"label": "Use a tool", "goal": s5_use_tool.goal, "grade": s5_use_tool.grade},
    "s6": {
        "label": "Fact resolve modes",
        "goal": s6_fact_resolve_modes.goal,
        "grade": s6_fact_resolve_modes.grade,
    },
    "s7": {
        "label": "Federation sync",
        "goal": s7_federation_sync.goal,
        "grade": s7_federation_sync.grade,
    },
    "s8": {
        "label": "HITL approval",
        "goal": s8_hitl_approval.goal,
        "grade": s8_hitl_approval.grade,
    },
    "s9": {
        "label": "Wallet budget",
        "goal": s9_wallet_budget.goal,
        "grade": s9_wallet_budget.grade,
    },
}
