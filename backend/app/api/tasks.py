from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.exceptions import NotFoundError
from app.models.task import Task
from app.schemas.task import TaskResponse, TaskSummary

router = APIRouter()


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
