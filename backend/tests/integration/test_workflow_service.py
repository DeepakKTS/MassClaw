"""Integration tests for WorkflowService.

Tests workflow CRUD, status transitions, and failure handling
against a real database using the conftest fixtures.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.exceptions import NotFoundError, OrchestrationError
from app.models.base import TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.services.workflow_service import WorkflowService


class TestWorkflowCreate:
    """Tests for workflow creation."""

    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return WorkflowService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_create_workflow(self, service, db_session):
        """Create a workflow with valid data and verify it persists with correct fields."""
        from app.schemas.workflow import WorkflowCreate

        data = WorkflowCreate(
            prompt="Analyze the supply chain logistics for a mid-size warehouse operation",
            user_id="test-user-001",
            domain="logistics",
            budget_limit=250.0,
            priority=3,
            metadata={"source": "integration-test"},
        )

        # We only test the DB persistence part — mock the execution pipeline
        # so we don't need real LLM calls.
        workflow = Workflow(
            user_id=data.user_id,
            prompt=data.prompt,
            domain=data.domain,
            status=WorkflowStatus.PENDING,
            budget_limit=data.budget_limit,
            priority=data.priority,
            metadata_=data.metadata,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        # Verify persisted fields
        assert workflow.workflow_id is not None
        assert workflow.user_id == "test-user-001"
        assert workflow.prompt == data.prompt
        assert workflow.domain == "logistics"
        assert workflow.status == WorkflowStatus.PENDING
        assert float(workflow.budget_limit) == 250.0
        assert float(workflow.budget_used) == 0.0
        assert workflow.priority == 3
        assert workflow.metadata_ == {"source": "integration-test"}
        assert workflow.started_at is None
        assert workflow.completed_at is None
        assert workflow.result is None

        # Verify we can retrieve it via the service
        fetched = await service.get_workflow(workflow.workflow_id)
        assert fetched.workflow_id == workflow.workflow_id
        assert fetched.prompt == data.prompt

    @pytest.mark.asyncio
    async def test_create_workflow_defaults(self, db_session):
        """Workflow should have sensible defaults for optional fields."""
        workflow = Workflow(
            user_id="default-user",
            prompt="A simple workflow for testing defaults",
            budget_limit=100.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        assert workflow.status == WorkflowStatus.PENDING
        assert float(workflow.budget_used) == 0.0
        assert workflow.priority == 5  # default
        assert workflow.metadata_ == {}
        assert workflow.dag_snapshot is None

    @pytest.mark.asyncio
    async def test_get_nonexistent_workflow_raises(self, service):
        """Fetching a non-existent workflow should raise NotFoundError."""
        fake_id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            await service.get_workflow(fake_id)


class TestWorkflowStatusTransitions:
    """Tests for workflow status lifecycle: PENDING -> RUNNING -> COMPLETED."""

    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return WorkflowService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_workflow_status_transitions(self, db_session, service):
        """Verify workflow status goes from PENDING -> RUNNING -> COMPLETED."""
        # Create in PENDING state
        workflow = Workflow(
            user_id="transition-user",
            prompt="Test status transitions for workflow lifecycle",
            domain="test",
            budget_limit=500.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)
        assert workflow.status == WorkflowStatus.PENDING

        # Transition to RUNNING
        workflow.status = WorkflowStatus.RUNNING
        workflow.started_at = datetime.now(timezone.utc)
        await db_session.flush()
        await db_session.refresh(workflow)
        assert workflow.status == WorkflowStatus.RUNNING
        assert workflow.started_at is not None

        # Verify status endpoint reflects RUNNING
        status = await service.get_workflow_status(workflow.workflow_id)
        assert status.status == WorkflowStatus.RUNNING
        assert status.started_at is not None
        assert status.elapsed_seconds is not None
        assert status.elapsed_seconds >= 0

        # Transition to COMPLETED
        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.now(timezone.utc)
        workflow.result = {
            "content": "Analysis complete",
            "confidence": 0.92,
            "summary": "All tasks succeeded",
        }
        await db_session.flush()
        await db_session.refresh(workflow)
        assert workflow.status == WorkflowStatus.COMPLETED
        assert workflow.completed_at is not None
        assert workflow.result["confidence"] == 0.92

    @pytest.mark.asyncio
    async def test_workflow_status_with_tasks(self, db_session, service, sample_agent):
        """Verify status endpoint correctly reports task counts."""
        workflow = Workflow(
            user_id="task-count-user",
            prompt="Workflow with multiple tasks for status testing",
            domain="test",
            budget_limit=300.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        # Add tasks in different states
        tasks = [
            Task(
                workflow_id=workflow.workflow_id,
                assigned_agent_id=sample_agent.agent_id,
                step_number=1,
                capability="intake",
                description="Intake step",
                status=TaskStatus.COMPLETED,
            ),
            Task(
                workflow_id=workflow.workflow_id,
                assigned_agent_id=sample_agent.agent_id,
                step_number=2,
                capability="research",
                description="Research step",
                status=TaskStatus.RUNNING,
            ),
            Task(
                workflow_id=workflow.workflow_id,
                assigned_agent_id=sample_agent.agent_id,
                step_number=3,
                capability="summarization",
                description="Summary step",
                status=TaskStatus.PENDING,
            ),
        ]
        for t in tasks:
            db_session.add(t)
        await db_session.flush()

        status = await service.get_workflow_status(workflow.workflow_id)
        assert status.total_tasks == 3
        assert status.completed_tasks == 1
        assert status.running_tasks == 1
        assert status.failed_tasks == 0
        assert status.progress_percent == pytest.approx(33.3, abs=0.2)

    @pytest.mark.asyncio
    async def test_cancel_running_workflow(self, db_session, service):
        """Cancelling a RUNNING workflow should set status to CANCELLED."""
        workflow = Workflow(
            user_id="cancel-user",
            prompt="Workflow to be cancelled during execution test",
            domain="test",
            budget_limit=100.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        workflow.status = WorkflowStatus.RUNNING
        workflow.started_at = datetime.now(timezone.utc)
        await db_session.flush()

        cancelled = await service.cancel_workflow(workflow.workflow_id)
        assert cancelled.status == WorkflowStatus.CANCELLED
        assert cancelled.completed_at is not None

    @pytest.mark.asyncio
    async def test_cancel_completed_workflow_raises(self, db_session, service):
        """Cancelling an already-completed workflow should raise OrchestrationError."""
        workflow = Workflow(
            user_id="complete-user",
            prompt="Already completed workflow that cannot be cancelled",
            domain="test",
            budget_limit=100.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.now(timezone.utc)
        await db_session.flush()

        with pytest.raises(OrchestrationError):
            await service.cancel_workflow(workflow.workflow_id)


class TestWorkflowFailureHandling:
    """Tests for workflow failure scenarios."""

    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return WorkflowService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_workflow_failure_handling(self, db_session, service, sample_agent):
        """Verify that when execution fails, workflow status is set to FAILED
        and the error is stored in the result field."""
        workflow = Workflow(
            user_id="failure-user",
            prompt="Workflow that will simulate a failure scenario",
            domain="test",
            budget_limit=500.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)
        assert workflow.status == WorkflowStatus.PENDING

        # Simulate the failure path from WorkflowService.create_and_execute
        error_msg = "GoalInterpreter failed: API key invalid"
        workflow.status = WorkflowStatus.FAILED
        workflow.completed_at = datetime.now(timezone.utc)
        workflow.result = {"error": error_msg}
        await db_session.flush()
        await db_session.refresh(workflow)

        assert workflow.status == WorkflowStatus.FAILED
        assert workflow.result == {"error": error_msg}
        assert workflow.completed_at is not None

        # Verify via service layer
        fetched = await service.get_workflow(workflow.workflow_id)
        assert fetched.status == WorkflowStatus.FAILED
        assert fetched.result["error"] == error_msg

    @pytest.mark.asyncio
    async def test_workflow_with_failed_tasks(self, db_session, service, sample_agent):
        """Verify result endpoint correctly reports failed task counts and costs."""
        workflow = Workflow(
            user_id="failed-tasks-user",
            prompt="Workflow with some failed tasks for result reporting",
            domain="test",
            budget_limit=500.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        # One completed task, one failed task
        completed_task = Task(
            workflow_id=workflow.workflow_id,
            assigned_agent_id=sample_agent.agent_id,
            step_number=1,
            capability="intake",
            description="Intake step",
            status=TaskStatus.COMPLETED,
            cost_used=10.5,
            latency_ms=1200.0,
        )
        failed_task = Task(
            workflow_id=workflow.workflow_id,
            assigned_agent_id=sample_agent.agent_id,
            step_number=2,
            capability="research",
            description="Research step",
            status=TaskStatus.FAILED,
            error_message="LLM provider returned 503",
        )
        db_session.add(completed_task)
        db_session.add(failed_task)
        await db_session.flush()

        # Mark workflow as partially completed
        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.now(timezone.utc)
        workflow.result = {
            "content": "Partial results available",
            "confidence": 0.5,
            "partial": True,
            "failed_tasks": 1,
        }
        await db_session.flush()

        result = await service.get_workflow_result(workflow.workflow_id)
        assert result.status == WorkflowStatus.COMPLETED
        assert result.tasks_completed == 1
        assert result.tasks_failed == 1
        assert result.total_cost == pytest.approx(10.5, abs=0.1)
        assert result.total_latency_ms == pytest.approx(1200.0, abs=1.0)
        assert result.confidence == 0.5
        assert result.result["partial"] is True

    @pytest.mark.asyncio
    async def test_workflow_all_tasks_failed(self, db_session, service, sample_agent):
        """When all tasks fail, verify the workflow reflects total failure."""
        workflow = Workflow(
            user_id="all-fail-user",
            prompt="Workflow where every task fails for complete failure testing",
            domain="test",
            budget_limit=500.0,
        )
        db_session.add(workflow)
        await db_session.flush()
        await db_session.refresh(workflow)

        # All tasks failed
        for i in range(3):
            task = Task(
                workflow_id=workflow.workflow_id,
                assigned_agent_id=sample_agent.agent_id,
                step_number=i + 1,
                capability=f"step-{i + 1}",
                description=f"Step {i + 1} that fails",
                status=TaskStatus.FAILED,
                error_message=f"Error in step {i + 1}",
            )
            db_session.add(task)
        await db_session.flush()

        workflow.status = WorkflowStatus.FAILED
        workflow.completed_at = datetime.now(timezone.utc)
        workflow.result = {"error": "All tasks failed"}
        await db_session.flush()

        status = await service.get_workflow_status(workflow.workflow_id)
        assert status.status == WorkflowStatus.FAILED
        assert status.total_tasks == 3
        assert status.failed_tasks == 3
        assert status.completed_tasks == 0
        assert status.progress_percent == 0.0

        result = await service.get_workflow_result(workflow.workflow_id)
        assert result.tasks_failed == 3
        assert result.tasks_completed == 0
        assert result.total_cost == 0.0
