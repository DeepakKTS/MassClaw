"""Unit tests for :class:`SchedulerPausedForApproval`.

The sentinel is the control-flow primitive that unwinds the scheduler
background task when Phase 1.5 decides to wait for a human. These
tests pin the shape so callers (``_execute_workflow_background`` and
``_resume_run_background`` in :mod:`app.api.workflows`) can depend on
the attributes being available.
"""

from __future__ import annotations

import pytest

from app.orchestration.scheduler import SchedulerPausedForApproval


class TestSchedulerPausedForApproval:
    def test_carries_identifying_fields(self) -> None:
        exc = SchedulerPausedForApproval(
            workflow_id="wf-1",
            node_id="task-2",
            request_id="req-123",
            checkpoint_hash="z6Mk-hash",
        )
        assert exc.workflow_id == "wf-1"
        assert exc.node_id == "task-2"
        assert exc.request_id == "req-123"
        assert exc.checkpoint_hash == "z6Mk-hash"

    def test_checkpoint_hash_may_be_none(self) -> None:
        exc = SchedulerPausedForApproval(
            workflow_id="wf-1",
            node_id="task-2",
            request_id="req-123",
            checkpoint_hash=None,
        )
        assert exc.checkpoint_hash is None

    def test_is_an_exception_and_can_be_raised_and_caught(self) -> None:
        with pytest.raises(SchedulerPausedForApproval) as info:
            raise SchedulerPausedForApproval(
                workflow_id="wf-x",
                node_id="n-1",
                request_id="r-1",
                checkpoint_hash=None,
            )
        assert info.value.workflow_id == "wf-x"

    def test_str_includes_useful_breadcrumb(self) -> None:
        exc = SchedulerPausedForApproval(
            workflow_id="wf-1",
            node_id="task-2",
            request_id="req-123",
            checkpoint_hash=None,
        )
        message = str(exc)
        assert "wf-1" in message
        assert "task-2" in message
        assert "req-123" in message
