from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.dependencies import get_project_service
from app.services.project_service import ProjectService

router = APIRouter()


# ── Request / Response Schemas ───────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: str = Field(default="")
    owner_id: str = Field(default="default", max_length=255)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, max_length=50)
    owner_id: str | None = Field(default=None, max_length=255)
    metadata_: dict | None = Field(default=None, alias="metadata")


class ProjectOut(BaseModel):
    project_id: uuid.UUID
    name: str
    description: str
    status: str
    owner_id: str
    metadata_: dict = Field(alias="metadata")
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True
        populate_by_name = True


class BacklogTaskCreate(BaseModel):
    title: str = Field(..., max_length=500)
    description: str = Field(default="")
    priority: int = Field(default=3, ge=1, le=5)
    labels: list[str] = Field(default_factory=list)


class BacklogTaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    story_points: int | None = None
    labels: list[str] | None = None
    status: str | None = Field(default=None, max_length=50)


class BacklogTaskOut(BaseModel):
    backlog_task_id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str
    priority: int
    story_points: int | None = None
    labels: list | dict
    status: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


class SprintCreate(BaseModel):
    name: str = Field(..., max_length=255)
    goal: str | None = None
    start_date: date
    end_date: date


class SprintOut(BaseModel):
    sprint_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    goal: str | None = None
    start_date: date
    end_date: date
    status: str
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


class SprintTaskAdd(BaseModel):
    backlog_task_id: uuid.UUID
    assigned_to: str | None = None


class SprintTaskUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=50)
    position: int | None = None
    assigned_to: str | None = None


class SprintTaskOut(BaseModel):
    sprint_task_id: uuid.UUID
    sprint_id: uuid.UUID
    backlog_task_id: uuid.UUID
    workflow_id: uuid.UUID | None = None
    status: str
    assigned_to: str | None = None
    position: int
    created_at: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


class SprintTaskExecute(BaseModel):
    """Body for the execute endpoint -- triggers a new workflow for this task."""

    prompt: str = Field(..., description="Prompt for the workflow execution")
    domain: str | None = None
    budget_limit: float = Field(default=1.0, ge=0)


# ── Project Endpoints ────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    """Create a new project."""
    project = await service.create_project(
        name=body.name,
        description=body.description,
        owner_id=body.owner_id,
    )
    return ProjectOut.model_validate(project)


@router.get("")
async def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: ProjectService = Depends(get_project_service),
) -> dict:
    """List projects with pagination."""
    from app.schemas.common import PaginationParams

    pagination = PaginationParams(page=page, page_size=page_size)
    result = await service.list_projects(pagination)
    return {
        "items": [ProjectOut.model_validate(p) for p in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    """Get a project by ID."""
    project = await service.get_project(project_id)
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    """Update a project's mutable fields."""
    fields = body.model_dump(exclude_unset=True)
    # Handle the alias: metadata -> metadata_
    if "metadata" in fields:
        fields["metadata_"] = fields.pop("metadata")
    project = await service.update_project(project_id, **fields)
    return ProjectOut.model_validate(project)


# ── Backlog Endpoints ────────────────────────────────────────────────


@router.post("/{project_id}/backlog", status_code=201)
async def create_backlog_task(
    project_id: uuid.UUID,
    body: BacklogTaskCreate,
    service: ProjectService = Depends(get_project_service),
) -> BacklogTaskOut:
    """Create a backlog task for the project."""
    task = await service.create_backlog_task(
        project_id=project_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        labels=body.labels,
    )
    return BacklogTaskOut.model_validate(task)


@router.get("/{project_id}/backlog")
async def list_backlog(
    project_id: uuid.UUID,
    status: str | None = Query(default=None, description="Filter by status"),
    service: ProjectService = Depends(get_project_service),
) -> list[BacklogTaskOut]:
    """List backlog tasks for a project with optional status filter."""
    tasks = await service.list_backlog(project_id, status_filter=status)
    return [BacklogTaskOut.model_validate(t) for t in tasks]


@router.patch("/backlog/{backlog_task_id}")
async def update_backlog_task(
    backlog_task_id: uuid.UUID,
    body: BacklogTaskUpdate,
    service: ProjectService = Depends(get_project_service),
) -> BacklogTaskOut:
    """Update a backlog task."""
    fields = body.model_dump(exclude_unset=True)
    task = await service.update_backlog_task(backlog_task_id, **fields)
    return BacklogTaskOut.model_validate(task)


@router.delete("/backlog/{backlog_task_id}", status_code=204, response_model=None)
async def delete_backlog_task(
    backlog_task_id: uuid.UUID,
    service: ProjectService = Depends(get_project_service),
) -> None:
    """Delete a backlog task."""
    await service.delete_backlog_task(backlog_task_id)


# ── Sprint Endpoints ─────────────────────────────────────────────────


@router.post("/{project_id}/sprints", status_code=201)
async def create_sprint(
    project_id: uuid.UUID,
    body: SprintCreate,
    service: ProjectService = Depends(get_project_service),
) -> SprintOut:
    """Create a sprint for the project."""
    sprint = await service.create_sprint(
        project_id=project_id,
        name=body.name,
        goal=body.goal,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    return SprintOut.model_validate(sprint)


@router.get("/{project_id}/sprints")
async def list_sprints(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_project_service),
) -> list[SprintOut]:
    """List all sprints for a project."""
    sprints = await service.list_sprints(project_id)
    return [SprintOut.model_validate(s) for s in sprints]


# ── Sprint Task Endpoints ────────────────────────────────────────────


@router.post("/sprints/{sprint_id}/tasks", status_code=201)
async def add_task_to_sprint(
    sprint_id: uuid.UUID,
    body: SprintTaskAdd,
    service: ProjectService = Depends(get_project_service),
) -> SprintTaskOut:
    """Add a backlog task to a sprint."""
    sprint_task = await service.add_task_to_sprint(
        sprint_id=sprint_id,
        backlog_task_id=body.backlog_task_id,
        assigned_to=body.assigned_to,
    )
    return SprintTaskOut.model_validate(sprint_task)


@router.patch("/sprints/tasks/{sprint_task_id}")
async def update_sprint_task(
    sprint_task_id: uuid.UUID,
    body: SprintTaskUpdate,
    service: ProjectService = Depends(get_project_service),
) -> SprintTaskOut:
    """Update a sprint task (status, position, assigned_to)."""
    sprint_task = await service.update_sprint_task(
        sprint_task_id=sprint_task_id,
        status=body.status,
        position=body.position,
        assigned_to=body.assigned_to,
    )
    return SprintTaskOut.model_validate(sprint_task)


@router.delete("/sprints/tasks/{sprint_task_id}", status_code=204, response_model=None)
async def remove_sprint_task(
    sprint_task_id: uuid.UUID,
    service: ProjectService = Depends(get_project_service),
) -> None:
    """Remove a task from a sprint."""
    await service.remove_sprint_task(sprint_task_id)


@router.post("/sprints/tasks/{sprint_task_id}/execute", status_code=201)
async def execute_sprint_task(
    sprint_task_id: uuid.UUID,
    body: SprintTaskExecute,
    service: ProjectService = Depends(get_project_service),
) -> SprintTaskOut:
    """Link a sprint task to a new workflow execution.

    Creates a workflow for the given prompt and links it to the sprint task,
    setting the task status to in_progress.
    """
    from app.core.database import db_session_context
    from app.core.redis import get_redis_manager
    from app.schemas.workflow import WorkflowCreate
    from app.services.workflow_service import WorkflowService

    # Create the workflow in its own session (execution involves many commits)
    redis = get_redis_manager().get_cache_client()
    async with db_session_context() as wf_session:
        wf_service = WorkflowService(wf_session, redis)
        workflow = await wf_service.create_and_execute(
            WorkflowCreate(
                prompt=body.prompt,
                domain=body.domain,
                budget_limit=body.budget_limit,
            )
        )
        workflow_id = workflow.workflow_id

    # Link the workflow to the sprint task
    sprint_task = await service.link_workflow(sprint_task_id, workflow_id)
    return SprintTaskOut.model_validate(sprint_task)
