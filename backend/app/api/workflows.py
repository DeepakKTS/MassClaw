from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.database import db_session_context, get_db_session
from app.core.events import EventBus
from app.core.redis import get_redis_manager
from app.dependencies import get_workflow_service
from app.models.base import WorkflowStatus
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowResponse,
    WorkflowResultResponse,
    WorkflowStatusResponse,
)
from app.services.workflow_service import WorkflowService

router = APIRouter()


async def _execute_workflow_background(workflow_id: str, data: WorkflowCreate) -> None:
    """Background task that executes the workflow after creation."""
    import asyncio
    from app.core.logging import get_logger
    logger = get_logger("workflow_bg")
    try:
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            from sqlalchemy import select
            from app.models.workflow import Workflow as WfModel
            result = await session.execute(select(WfModel).where(WfModel.workflow_id == workflow_id))
            workflow = result.scalar_one()
            service = WorkflowService(session, redis)
            # Run the intelligence pipeline
            from app.intelligence.goal_interpreter import GoalInterpreter
            from app.intelligence.planner import AdaptivePlanner
            from app.intelligence.strategy_router import StrategyRouter
            goal = await GoalInterpreter().interpret(data.prompt)
            plan = AdaptivePlanner().plan(goal, float(data.budget_limit))
            router = StrategyRouter(session, redis)
            await router.execute(plan, workflow)
    except Exception as e:
        logger.error("workflow_background_failed", workflow_id=workflow_id, error=str(e))
        # Mark workflow as failed
        try:
            async with db_session_context() as session:
                from sqlalchemy import select, update
                from app.models.workflow import Workflow as WfModel
                from app.models.base import WorkflowStatus
                from datetime import datetime, timezone
                await session.execute(
                    update(WfModel).where(WfModel.workflow_id == workflow_id)
                    .values(status=WorkflowStatus.FAILED, result={"error": str(e)},
                            completed_at=datetime.now(timezone.utc))
                )
        except Exception:
            pass


@router.post("", response_model=WorkflowResponse, status_code=201)
async def create_workflow(
    data: WorkflowCreate,
    background_tasks: BackgroundTasks,
) -> WorkflowResponse:
    """Create a workflow and start execution in the background.

    Returns immediately with the workflow in PENDING status.
    Execution happens asynchronously — monitor via GET /workflows/{id}/status
    or the SSE stream at GET /workflows/{id}/stream.
    """
    import uuid as _uuid
    redis = get_redis_manager().get_cache_client()
    async with db_session_context() as session:
        from app.models.workflow import Workflow as WfModel
        from app.models.base import WorkflowStatus
        workflow = WfModel(
            user_id=data.user_id,
            prompt=data.prompt,
            domain=data.domain,
            status=WorkflowStatus.PENDING,
            budget_limit=data.budget_limit,
            priority=data.priority,
            metadata_=data.metadata,
        )
        session.add(workflow)
        await session.flush()
        await session.refresh(workflow)
        wf_id = str(workflow.workflow_id)
        response = WorkflowResponse.model_validate(workflow)

    # Launch execution in background — returns immediately to client
    background_tasks.add_task(_execute_workflow_background, wf_id, data)
    return response


@router.get("", response_model=PaginatedResponse[WorkflowResponse])
async def list_workflows(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: WorkflowStatus | None = Query(default=None),
    service: WorkflowService = Depends(get_workflow_service),
) -> PaginatedResponse[WorkflowResponse]:
    """List workflows with pagination and optional status filter."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.list_workflows(pagination, status=status)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: uuid.UUID,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    """Get a workflow by ID with full details."""
    workflow = await service.get_workflow(workflow_id)
    return WorkflowResponse.model_validate(workflow)


@router.get("/{workflow_id}/status", response_model=WorkflowStatusResponse)
async def get_workflow_status(
    workflow_id: uuid.UUID,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowStatusResponse:
    """Get workflow status with task progress breakdown."""
    return await service.get_workflow_status(workflow_id)


@router.get("/{workflow_id}/result", response_model=WorkflowResultResponse)
async def get_workflow_result(
    workflow_id: uuid.UUID,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResultResponse:
    """Get the final synthesized result of a completed workflow."""
    return await service.get_workflow_result(workflow_id)


@router.delete("/{workflow_id}", response_model=WorkflowResponse)
async def cancel_workflow(
    workflow_id: uuid.UUID,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    """Cancel a running or pending workflow."""
    workflow = await service.cancel_workflow(workflow_id)
    return WorkflowResponse.model_validate(workflow)


@router.get("/{workflow_id}/stream")
async def stream_workflow(workflow_id: uuid.UUID) -> EventSourceResponse:
    """Server-Sent Events stream for real-time workflow progress.

    Subscribes to Redis pub/sub and forwards events to the client.
    Sends heartbeat comments every 15s to keep the connection alive.
    Subscribes to massclaw:workflow:{id}:* to capture all workflow events.
    """

    async def event_generator() -> AsyncGenerator[dict, None]:
        # Send initial connection event
        yield {
            "event": "connected",
            "data": json.dumps({"workflow_id": str(workflow_id), "message": "SSE stream connected"}),
        }

        heartbeat_counter = 0
        async for event in EventBus.subscribe("workflow", str(workflow_id), "*"):
            yield {
                "event": event.event_type,
                "data": json.dumps(event.data, default=str),
                "id": event.event_id,
            }
            # Check if workflow completed
            if event.data.get("event") in ("workflow_completed", "workflow_failed", "workflow_cancelled"):
                yield {
                    "event": "stream_end",
                    "data": json.dumps({"reason": "workflow_terminal_state"}),
                }
                break

    return EventSourceResponse(event_generator())


@router.get("/{workflow_id}/reasoning")
async def get_reasoning_traces(
    workflow_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Get reasoning traces for a workflow."""
    from app.models.reasoning import ReasoningTrace
    result = await session.execute(
        select(ReasoningTrace)
        .where(ReasoningTrace.workflow_id == workflow_id)
        .order_by(ReasoningTrace.created_at)
    )
    traces = result.scalars().all()
    return [
        {
            "trace_id": str(t.trace_id),
            "iteration": t.iteration,
            "phase": t.phase,
            "event_type": t.event_type,
            "content": t.content,
            "confidence": t.confidence,
            "metadata": t.metadata_,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in traces
    ]
