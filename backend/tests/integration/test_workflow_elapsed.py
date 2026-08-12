"""Regression tests for ``elapsed_seconds`` on /workflows/{id}/status.

A live HITL workflow was observed reporting a static ``-27.5`` elapsed while
the backend log showed tasks completing. Both halves of that — the negative
value and the frozen one — come from the same root cause: ``completed_at`` is
never cleared, anywhere in the codebase, while ``started_at`` is rewritten on
every execution.

The sequence:

1. A workflow parks in AWAITING_APPROVAL, then the janitor times it out and
   sets ``completed_at = T1`` (app/safety/approval_janitor.py).
2. A human approves and resumes. ``WorkflowResumer`` set ``status = RUNNING``
   but left ``completed_at = T1`` in place.
3. The scheduler stamps a fresh ``started_at = T2``, which is now **after**
   T1.
4. ``get_workflow_status`` computed ``completed_at or now()``. Because T1 is
   truthy it never reached ``now()``, giving ``T1 - T2`` — negative, and
   frozen, since neither operand moves again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.models.base import WorkflowStatus
from app.models.workflow import Workflow
from app.services.workflow_service import WorkflowService


@pytest_asyncio.fixture
async def service(db_session, redis_client):
    return WorkflowService(db_session, redis_client)


async def _workflow(db_session, **kwargs) -> Workflow:
    defaults = {
        "user_id": "test-user",
        "prompt": "elapsed_seconds regression",
        "budget_limit": 100.0,
        "status": WorkflowStatus.RUNNING,
    }
    defaults.update(kwargs)
    wf = Workflow(**defaults)
    db_session.add(wf)
    await db_session.flush()
    await db_session.refresh(wf)
    return wf


class TestResumedWorkflowWithStaleCompletedAt:
    """The exact production repro."""

    @pytest.mark.asyncio
    async def test_elapsed_is_never_negative(self, service, db_session):
        now = datetime.now(UTC)
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.RUNNING,
            completed_at=now - timedelta(seconds=90),  # janitor failed it earlier
            started_at=now - timedelta(seconds=30),  # scheduler re-stamped on resume
        )

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds is not None
        assert status.elapsed_seconds >= 0, f"got {status.elapsed_seconds}, the reported -27.5 class of bug"

    @pytest.mark.asyncio
    async def test_elapsed_advances_rather_than_freezing(self, service, db_session):
        """A RUNNING workflow measures against now(), so a stale completed_at
        cannot pin the value."""
        now = datetime.now(UTC)
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.RUNNING,
            completed_at=now - timedelta(seconds=90),
            started_at=now - timedelta(seconds=30),
        )

        first = (await service.get_workflow_status(wf.workflow_id)).elapsed_seconds
        assert first is not None
        # ~30s in, measured from now() — not the 90s-ago completed_at.
        assert 25 <= first <= 40, f"expected elapsed measured against now(), got {first}"

    @pytest.mark.asyncio
    async def test_awaiting_approval_also_measures_against_now(self, service, db_session):
        now = datetime.now(UTC)
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.AWAITING_APPROVAL,
            completed_at=now - timedelta(seconds=120),
            started_at=now - timedelta(seconds=45),
        )

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds is not None
        assert status.elapsed_seconds >= 0
        assert 40 <= status.elapsed_seconds <= 55


class TestTerminalWorkflows:
    """For a finished workflow ``completed_at`` is the right end point."""

    @pytest.mark.asyncio
    async def test_completed_workflow_uses_completed_at(self, service, db_session):
        now = datetime.now(UTC)
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.COMPLETED,
            started_at=now - timedelta(seconds=300),
            completed_at=now - timedelta(seconds=60),
        )

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds == pytest.approx(240, abs=2)

    @pytest.mark.asyncio
    async def test_failed_workflow_uses_completed_at(self, service, db_session):
        now = datetime.now(UTC)
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.FAILED,
            started_at=now - timedelta(seconds=100),
            completed_at=now - timedelta(seconds=40),
        )

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds == pytest.approx(60, abs=2)

    @pytest.mark.asyncio
    async def test_terminal_without_completed_at_falls_back_to_now(self, service, db_session):
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(seconds=50),
            completed_at=None,
        )

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds is not None
        assert status.elapsed_seconds >= 0


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_elapsed_is_reported_not_swallowed(self, service, db_session):
        """``round(elapsed, 1) if elapsed else None`` turned a real 0.0 into
        None — a sub-100ms workflow looked like it had never started."""
        now = datetime.now(UTC)
        wf = await _workflow(
            db_session,
            status=WorkflowStatus.COMPLETED,
            started_at=now,
            completed_at=now,
        )

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds == 0.0
        assert status.elapsed_seconds is not None

    @pytest.mark.asyncio
    async def test_no_started_at_yields_none(self, service, db_session):
        wf = await _workflow(db_session, status=WorkflowStatus.PENDING, started_at=None)

        status = await service.get_workflow_status(wf.workflow_id)

        assert status.elapsed_seconds is None
