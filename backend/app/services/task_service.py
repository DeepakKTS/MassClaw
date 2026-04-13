from __future__ import annotations

import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import NotFoundError, ValidationError
from app.models.task import Task
from app.models.base import TaskStatus
from app.orchestration.state_machine import validate_transition, get_valid_override_actions

logger = get_logger(__name__)


class TaskService:
    """Task lifecycle management with manual override support."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def get_task(self, task_id: uuid.UUID) -> Task:
        result = await self.session.execute(
            select(Task).where(Task.task_id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("Task", str(task_id))
        return task

    async def override_task(
        self,
        task_id: uuid.UUID,
        action: str,
        reason: str | None = None,
        force_output: dict | None = None,
    ) -> Task:
        """Override a task's status with row-level locking for concurrency safety.

        Actions: retry, skip, force_complete, cancel
        Uses SELECT FOR UPDATE to prevent race conditions with the scheduler.
        """
        if action not in ("retry", "skip", "force_complete", "cancel"):
            raise ValidationError(f"Invalid override action: {action}. Must be one of: retry, skip, force_complete, cancel")

        # Row-level lock to prevent concurrent modifications
        result = await self.session.execute(
            select(Task).where(Task.task_id == task_id).with_for_update()
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("Task", str(task_id))

        # Check valid override actions for current state
        valid = get_valid_override_actions(task.status)
        if action not in valid:
            raise ValidationError(
                f"Cannot '{action}' task in '{task.status.value}' state. "
                f"Valid actions: {valid}"
            )

        # Validate and get new status
        new_status = validate_transition(task.status, action)
        old_status = task.status

        # Apply the transition
        task.status = new_status

        if action == "retry":
            task.retry_count += 1
            task.error_message = None
            task.blocked_reason = None
            task.blocked_by_task_id = None

        elif action == "skip":
            task.error_message = reason or "Manually skipped"

        elif action == "force_complete":
            if force_output:
                task.output = force_output
            task.confidence = 1.0  # Manually verified
            task.completed_at = datetime.now(timezone.utc)
            task.error_message = None

        elif action == "cancel":
            task.error_message = reason or "Manually cancelled"

        await self.session.flush()
        await self.session.refresh(task)

        logger.info(
            "task_overridden",
            task_id=str(task_id),
            action=action,
            old_status=old_status.value,
            new_status=new_status.value,
            reason=reason,
        )

        # Publish event
        await EventBus.publish_dict(
            ["task", str(task_id), "overridden"],
            "task.overridden",
            {
                "task_id": str(task_id),
                "workflow_id": str(task.workflow_id),
                "action": action,
                "old_status": old_status.value,
                "new_status": new_status.value,
                "reason": reason,
            },
        )

        return task

    async def get_override_actions(self, task_id: uuid.UUID) -> list[str]:
        """Get valid override actions for a task's current state."""
        task = await self.get_task(task_id)
        return get_valid_override_actions(task.status)
