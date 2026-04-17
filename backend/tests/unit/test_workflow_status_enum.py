"""Unit tests for the ``WorkflowStatus`` enum declaration.

The Postgres enum type has historically drifted from the Python enum,
most recently when migration ``c8e4a91d2f70`` added ``awaiting_approval``
to the DB side without mirroring it in Python. These tests lock the
Python declaration so future drift is caught at import time.
"""

from __future__ import annotations

from app.models.base import WorkflowStatus


class TestWorkflowStatusEnum:
    def test_awaiting_approval_member_exists(self) -> None:
        assert WorkflowStatus.AWAITING_APPROVAL.value == "awaiting_approval"

    def test_known_members(self) -> None:
        values = {m.value for m in WorkflowStatus}
        assert values == {
            "pending",
            "decomposing",
            "running",
            "paused",
            "completed",
            "failed",
            "cancelled",
            "awaiting_approval",
        }

    def test_string_comparison_works(self) -> None:
        # WorkflowStatus is a str-enum; downstream code compares to raw strings.
        assert WorkflowStatus.AWAITING_APPROVAL == "awaiting_approval"
