from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.dependencies import get_task_service, get_task_test_service
from app.exceptions import NotFoundError
from app.models.task import Task
from app.schemas.task import TaskResponse, TaskSummary
from app.services.task_service import TaskService
from app.services.task_test_service import TaskTestService

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TaskOverrideRequest(BaseModel):
    action: str = Field(..., pattern="^(retry|skip|force_complete|cancel)$")
    reason: str | None = Field(default=None, max_length=500)
    force_output: dict | None = None


class TaskTestRequest(BaseModel):
    test_name: str = Field(..., min_length=1, max_length=255)
    test_type: str = Field(..., pattern="^(assertion|regex|schema|llm_eval)$")
    assertion: dict


class TaskTestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    test_id: uuid.UUID
    task_id: uuid.UUID
    test_name: str
    test_type: str
    status: str
    assertion: dict
    actual_value: str | None
    error_message: str | None
    execution_time_ms: float | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> TaskResponse:
    """Get a single task by ID."""
    result = await session.execute(
        select(Task).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task", str(task_id))
    return TaskResponse.model_validate(task)


@router.get("/workflow/{workflow_id}", response_model=list[TaskSummary])
async def get_workflow_tasks(
    workflow_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[TaskSummary]:
    """Get all tasks for a workflow, ordered by step number."""
    result = await session.execute(
        select(Task)
        .where(Task.workflow_id == workflow_id)
        .order_by(Task.step_number)
    )
    tasks = list(result.scalars().all())
    return [TaskSummary.model_validate(t) for t in tasks]


# ---------------------------------------------------------------------------
# Override endpoints
# ---------------------------------------------------------------------------


@router.post("/{task_id}/override", response_model=TaskResponse)
async def override_task(
    task_id: uuid.UUID,
    body: TaskOverrideRequest,
    svc: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Override a task's status (retry, skip, force_complete, cancel)."""
    task = await svc.override_task(
        task_id=task_id,
        action=body.action,
        reason=body.reason,
        force_output=body.force_output,
    )
    return TaskResponse.model_validate(task)


@router.get("/{task_id}/actions", response_model=list[str])
async def get_override_actions(
    task_id: uuid.UUID,
    svc: TaskService = Depends(get_task_service),
) -> list[str]:
    """Get valid override actions for a task's current state."""
    return await svc.get_override_actions(task_id)


# ---------------------------------------------------------------------------
# Test endpoints
# ---------------------------------------------------------------------------


@router.get("/{task_id}/tests", response_model=list[TaskTestResponse])
async def list_task_tests(
    task_id: uuid.UUID,
    svc: TaskTestService = Depends(get_task_test_service),
) -> list[TaskTestResponse]:
    """List all test results for a task."""
    tests = await svc.list_tests(task_id)
    return [TaskTestResponse.model_validate(t) for t in tests]


@router.post("/{task_id}/tests", response_model=TaskTestResponse)
async def run_task_test(
    task_id: uuid.UUID,
    body: TaskTestRequest,
    svc: TaskTestService = Depends(get_task_test_service),
) -> TaskTestResponse:
    """Run a test assertion against a task's output."""
    test = await svc.run_test(
        task_id=task_id,
        test_name=body.test_name,
        test_type=body.test_type,
        assertion=body.assertion,
    )
    return TaskTestResponse.model_validate(test)


@router.get("/{task_id}/tests/summary")
async def get_test_summary(
    task_id: uuid.UUID,
    svc: TaskTestService = Depends(get_task_test_service),
) -> dict:
    """Get test result summary for a task."""
    return await svc.get_summary(task_id)
