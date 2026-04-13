from __future__ import annotations

import uuid
from datetime import date

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import ConflictError, NotFoundError
from app.models.project import BacklogTask, Project, Sprint, SprintTask
from app.schemas.common import PaginatedResponse, PaginationParams

logger = get_logger(__name__)


class ProjectService:
    """Service layer for project planning: projects, backlog, sprints."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    # ── Projects ─────────────────────────────────────────────────────

    async def create_project(
        self,
        name: str,
        description: str = "",
        owner_id: str = "default",
    ) -> Project:
        """Create a new project. Raises ConflictError if name already exists."""
        existing = await self.session.execute(
            select(Project).where(Project.name == name)
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"Project with name '{name}' already exists")

        project = Project(name=name, description=description, owner_id=owner_id)
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        logger.info("project_created", project_id=str(project.project_id), name=name)
        return project

    async def list_projects(
        self, pagination: PaginationParams,
    ) -> PaginatedResponse:
        """List projects with pagination."""
        count_result = await self.session.execute(
            select(func.count()).select_from(Project)
        )
        total = count_result.scalar_one()

        result = await self.session.execute(
            select(Project)
            .order_by(Project.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        projects = list(result.scalars().all())

        return PaginatedResponse(
            items=projects,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def get_project(self, project_id: uuid.UUID) -> Project:
        """Get a project by ID."""
        result = await self.session.execute(
            select(Project).where(Project.project_id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise NotFoundError("Project", str(project_id))
        return project

    async def update_project(self, project_id: uuid.UUID, **fields: object) -> Project:
        """Update mutable project fields (name, description, status, owner_id, metadata_)."""
        project = await self.get_project(project_id)
        allowed = {"name", "description", "status", "owner_id", "metadata_"}
        for key, value in fields.items():
            if key in allowed and value is not None:
                setattr(project, key, value)
        await self.session.flush()
        await self.session.refresh(project)
        logger.info("project_updated", project_id=str(project_id))
        return project

    # ── Backlog ──────────────────────────────────────────────────────

    async def create_backlog_task(
        self,
        project_id: uuid.UUID,
        title: str,
        description: str = "",
        priority: int = 3,
        labels: list | None = None,
    ) -> BacklogTask:
        """Create a backlog task for the given project."""
        # Ensure project exists
        await self.get_project(project_id)

        task = BacklogTask(
            project_id=project_id,
            title=title,
            description=description,
            priority=priority,
            labels=labels or [],
        )
        self.session.add(task)
        await self.session.flush()
        await self.session.refresh(task)
        logger.info(
            "backlog_task_created",
            backlog_task_id=str(task.backlog_task_id),
            project_id=str(project_id),
        )
        return task

    async def list_backlog(
        self,
        project_id: uuid.UUID,
        status_filter: str | None = None,
    ) -> list[BacklogTask]:
        """List backlog tasks for a project, optionally filtered by status."""
        query = select(BacklogTask).where(BacklogTask.project_id == project_id)
        if status_filter:
            query = query.where(BacklogTask.status == status_filter)
        query = query.order_by(BacklogTask.priority.asc(), BacklogTask.created_at.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_backlog_task(
        self, backlog_task_id: uuid.UUID, **fields: object,
    ) -> BacklogTask:
        """Update mutable backlog-task fields."""
        result = await self.session.execute(
            select(BacklogTask).where(BacklogTask.backlog_task_id == backlog_task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("BacklogTask", str(backlog_task_id))

        allowed = {"title", "description", "priority", "story_points", "labels", "status"}
        for key, value in fields.items():
            if key in allowed and value is not None:
                setattr(task, key, value)

        await self.session.flush()
        await self.session.refresh(task)
        logger.info("backlog_task_updated", backlog_task_id=str(backlog_task_id))
        return task

    async def delete_backlog_task(self, backlog_task_id: uuid.UUID) -> None:
        """Delete a backlog task."""
        result = await self.session.execute(
            select(BacklogTask).where(BacklogTask.backlog_task_id == backlog_task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise NotFoundError("BacklogTask", str(backlog_task_id))
        await self.session.delete(task)
        await self.session.flush()
        logger.info("backlog_task_deleted", backlog_task_id=str(backlog_task_id))

    # ── Sprints ──────────────────────────────────────────────────────

    async def create_sprint(
        self,
        project_id: uuid.UUID,
        name: str,
        goal: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Sprint:
        """Create a sprint for the given project."""
        await self.get_project(project_id)

        if start_date is None or end_date is None:
            raise ValueError("start_date and end_date are required")

        sprint = Sprint(
            project_id=project_id,
            name=name,
            goal=goal,
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(sprint)
        await self.session.flush()
        await self.session.refresh(sprint)
        logger.info(
            "sprint_created",
            sprint_id=str(sprint.sprint_id),
            project_id=str(project_id),
        )
        return sprint

    async def list_sprints(self, project_id: uuid.UUID) -> list[Sprint]:
        """List all sprints for a project."""
        result = await self.session.execute(
            select(Sprint)
            .where(Sprint.project_id == project_id)
            .order_by(Sprint.start_date.asc())
        )
        return list(result.scalars().all())

    # ── Sprint Tasks ─────────────────────────────────────────────────

    async def add_task_to_sprint(
        self,
        sprint_id: uuid.UUID,
        backlog_task_id: uuid.UUID,
        assigned_to: str | None = None,
    ) -> SprintTask:
        """Add a backlog task to a sprint."""
        # Verify sprint exists
        sprint_result = await self.session.execute(
            select(Sprint).where(Sprint.sprint_id == sprint_id)
        )
        if sprint_result.scalar_one_or_none() is None:
            raise NotFoundError("Sprint", str(sprint_id))

        # Verify backlog task exists
        bt_result = await self.session.execute(
            select(BacklogTask).where(BacklogTask.backlog_task_id == backlog_task_id)
        )
        if bt_result.scalar_one_or_none() is None:
            raise NotFoundError("BacklogTask", str(backlog_task_id))

        # Determine next position
        max_pos_result = await self.session.execute(
            select(func.coalesce(func.max(SprintTask.position), -1))
            .where(SprintTask.sprint_id == sprint_id)
        )
        next_pos = max_pos_result.scalar_one() + 1

        sprint_task = SprintTask(
            sprint_id=sprint_id,
            backlog_task_id=backlog_task_id,
            assigned_to=assigned_to,
            position=next_pos,
        )
        self.session.add(sprint_task)
        await self.session.flush()
        await self.session.refresh(sprint_task)
        logger.info(
            "sprint_task_added",
            sprint_task_id=str(sprint_task.sprint_task_id),
            sprint_id=str(sprint_id),
        )
        return sprint_task

    async def update_sprint_task(
        self,
        sprint_task_id: uuid.UUID,
        status: str | None = None,
        position: int | None = None,
        assigned_to: str | None = None,
    ) -> SprintTask:
        """Update a sprint task's status, position, or assignee."""
        result = await self.session.execute(
            select(SprintTask).where(SprintTask.sprint_task_id == sprint_task_id)
        )
        sprint_task = result.scalar_one_or_none()
        if sprint_task is None:
            raise NotFoundError("SprintTask", str(sprint_task_id))

        if status is not None:
            sprint_task.status = status
        if position is not None:
            sprint_task.position = position
        if assigned_to is not None:
            sprint_task.assigned_to = assigned_to

        await self.session.flush()
        await self.session.refresh(sprint_task)
        logger.info("sprint_task_updated", sprint_task_id=str(sprint_task_id))
        return sprint_task

    async def remove_sprint_task(self, sprint_task_id: uuid.UUID) -> None:
        """Remove a task from a sprint."""
        result = await self.session.execute(
            select(SprintTask).where(SprintTask.sprint_task_id == sprint_task_id)
        )
        sprint_task = result.scalar_one_or_none()
        if sprint_task is None:
            raise NotFoundError("SprintTask", str(sprint_task_id))
        await self.session.delete(sprint_task)
        await self.session.flush()
        logger.info("sprint_task_removed", sprint_task_id=str(sprint_task_id))

    async def reorder_sprint_tasks(
        self, sprint_id: uuid.UUID, task_ids: list[uuid.UUID],
    ) -> None:
        """Bulk-reorder sprint tasks by setting positions according to the list order."""
        for position, task_id in enumerate(task_ids):
            result = await self.session.execute(
                select(SprintTask).where(
                    SprintTask.sprint_task_id == task_id,
                    SprintTask.sprint_id == sprint_id,
                )
            )
            sprint_task = result.scalar_one_or_none()
            if sprint_task is None:
                raise NotFoundError("SprintTask", str(task_id))
            sprint_task.position = position

        await self.session.flush()
        logger.info(
            "sprint_tasks_reordered",
            sprint_id=str(sprint_id),
            count=len(task_ids),
        )

    async def link_workflow(
        self, sprint_task_id: uuid.UUID, workflow_id: uuid.UUID,
    ) -> SprintTask:
        """Link a sprint task to a workflow execution."""
        result = await self.session.execute(
            select(SprintTask).where(SprintTask.sprint_task_id == sprint_task_id)
        )
        sprint_task = result.scalar_one_or_none()
        if sprint_task is None:
            raise NotFoundError("SprintTask", str(sprint_task_id))

        sprint_task.workflow_id = workflow_id
        sprint_task.status = "in_progress"
        await self.session.flush()
        await self.session.refresh(sprint_task)
        logger.info(
            "sprint_task_linked_to_workflow",
            sprint_task_id=str(sprint_task_id),
            workflow_id=str(workflow_id),
        )
        return sprint_task
