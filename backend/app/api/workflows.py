from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
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


MAX_WORKFLOW_TIMEOUT_SECONDS = 600  # 10 minutes max per workflow


async def _execute_workflow_background(workflow_id: str, data: WorkflowCreate) -> None:
    """Background task that executes the workflow with a hard timeout."""
    import asyncio

    from app.core.logging import get_logger

    logger = get_logger("workflow_bg")

    async def _run() -> None:
        redis = get_redis_manager().get_cache_client()
        async with db_session_context() as session:
            from sqlalchemy import select

            from app.models.workflow import Workflow as WfModel

            result = await session.execute(select(WfModel).where(WfModel.workflow_id == workflow_id))
            workflow = result.scalar_one()
            from app.intelligence.goal_interpreter import GoalInterpreter
            from app.intelligence.planner import AdaptivePlanner
            from app.intelligence.strategy_router import StrategyRouter

            goal = await GoalInterpreter().interpret(data.prompt)
            plan = AdaptivePlanner().plan(goal, float(data.budget_limit))
            strategy_router = StrategyRouter(session, redis)
            await strategy_router.execute(plan, workflow)

    try:
        await asyncio.wait_for(_run(), timeout=MAX_WORKFLOW_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.error("workflow_timeout", workflow_id=workflow_id, timeout=MAX_WORKFLOW_TIMEOUT_SECONDS)
        await _mark_workflow_failed(workflow_id, f"Workflow timed out after {MAX_WORKFLOW_TIMEOUT_SECONDS}s")
    except Exception as e:
        logger.error("workflow_background_failed", workflow_id=workflow_id, error=str(e), error_type=type(e).__name__)
        await _mark_workflow_failed(workflow_id, str(e))


async def _mark_workflow_failed(workflow_id: str, error_message: str) -> None:
    """Mark a workflow as failed. Separate function for reuse."""
    from app.core.logging import get_logger

    logger = get_logger("workflow_bg")
    try:
        async with db_session_context() as session:
            from datetime import datetime

            from sqlalchemy import update

            from app.models.base import WorkflowStatus
            from app.models.workflow import Workflow as WfModel

            await session.execute(
                update(WfModel)
                .where(WfModel.workflow_id == workflow_id)
                .values(status=WorkflowStatus.FAILED, result={"error": error_message}, completed_at=datetime.now(UTC))
            )
    except Exception as inner_err:
        logger.error("workflow_failure_marking_failed", workflow_id=workflow_id, error=str(inner_err))


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
    get_redis_manager().get_cache_client()  # Verify Redis is available
    async with db_session_context() as session:
        from app.models.base import WorkflowStatus
        from app.models.workflow import Workflow as WfModel

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


class TaskSubmitRequest(BaseModel):
    """Plain-English task submission for external agents (OpenClaw-compatible)."""

    instruction: str = Field(..., min_length=5, max_length=50000, description="What you want the agents to do")
    budget: float = Field(default=500, gt=0, le=10000, description="Maximum budget in credits")
    domain: str | None = Field(default=None, max_length=100, description="Domain hint (auto-detected if omitted)")
    priority: int = Field(default=5, ge=1, le=10, description="Priority (1=highest)")
    user_id: str = Field(default="external-agent", max_length=255)


# Mount this BEFORE the /{workflow_id} catch-all routes
@router.post("/submit")
async def submit_task(
    data: TaskSubmitRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Submit a plain-English task for multi-agent orchestration.

    This is the primary endpoint for external agents (OpenClaw, stock agents).
    MassClaw will automatically decompose, assign agents, execute, and synthesize.

    Returns a workflow_id to track progress via GET /workflows/{id}/status.
    """
    # Convert to internal WorkflowCreate
    wf_data = WorkflowCreate(
        prompt=data.instruction,
        budget_limit=data.budget,
        domain=data.domain,
        priority=data.priority,
        user_id=data.user_id,
    )

    get_redis_manager().get_cache_client()  # Verify Redis is available
    async with db_session_context() as session:
        from app.models.base import WorkflowStatus as WfStatus
        from app.models.workflow import Workflow as WfModel

        workflow = WfModel(
            user_id=wf_data.user_id,
            prompt=wf_data.prompt,
            domain=wf_data.domain,
            status=WfStatus.PENDING,
            budget_limit=wf_data.budget_limit,
            priority=wf_data.priority,
        )
        session.add(workflow)
        await session.flush()
        await session.refresh(workflow)
        wf_id = str(workflow.workflow_id)

    background_tasks.add_task(_execute_workflow_background, wf_id, wf_data)

    return {
        "workflow_id": wf_id,
        "status": "pending",
        "message": "Task accepted. Use workflow_id to track progress.",
        "track_url": f"/api/v1/workflows/{wf_id}/status",
        "result_url": f"/api/v1/workflows/{wf_id}/result",
    }


class EstimateBudgetRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=50000)


@router.post("/estimate-budget")
async def estimate_budget(data: EstimateBudgetRequest) -> dict:
    """Estimate the budget needed for a given prompt based on complexity analysis.

    Analyzes prompt length, keyword density, and implied task count
    to suggest an appropriate budget range.
    """
    prompt = data.prompt
    if not prompt.strip():
        return {"estimated_budget": 100, "estimated_ops": 2, "complexity": "simple", "confidence": 0.5}

    words = prompt.split()
    word_count = len(words)

    # Complexity signals
    complex_keywords = {
        "analyze",
        "research",
        "investigate",
        "compare",
        "evaluate",
        "comprehensive",
        "detailed",
        "thorough",
        "multi-step",
        "cross-reference",
        "risk",
        "compliance",
        "audit",
        "security",
        "optimization",
        "forecast",
        "strategy",
        "architecture",
        "design",
        "implement",
        "validate",
    }
    action_keywords = {
        "identify",
        "propose",
        "generate",
        "create",
        "build",
        "plan",
        "assess",
        "review",
        "check",
        "verify",
        "map",
        "extract",
        "summarize",
        "report",
        "brief",
        "schedule",
        "allocate",
        "estimate",
    }

    prompt_lower = prompt.lower()
    complex_count = sum(1 for kw in complex_keywords if kw in prompt_lower)
    action_count = sum(1 for kw in action_keywords if kw in prompt_lower)

    # Estimate ops based on signals
    if word_count < 10 and complex_count == 0:
        estimated_ops = 3
        complexity = "simple"
    elif word_count < 25 or (complex_count <= 1 and action_count <= 2):
        estimated_ops = 5
        complexity = "moderate"
    elif word_count < 50 or (complex_count <= 3 and action_count <= 4):
        estimated_ops = 7
        complexity = "complex"
    else:
        estimated_ops = max(8, min(12, action_count + complex_count + 2))
        complexity = "comprehensive"

    # Each op costs ~15-40 credits on average (varies by model/complexity)
    avg_cost_per_op = 25 if complexity in ("complex", "comprehensive") else 18
    estimated_budget = estimated_ops * avg_cost_per_op

    # Round to nearest 50
    estimated_budget = max(50, round(estimated_budget / 50) * 50)

    return {
        "estimated_budget": estimated_budget,
        "estimated_ops": estimated_ops,
        "complexity": complexity,
        "confidence": min(0.9, 0.5 + complex_count * 0.05 + action_count * 0.05),
    }


@router.post("/cleanup-stale")
async def cleanup_stale_workflows() -> dict:
    """Find and fail any workflows stuck in pending/running for > 10 minutes."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from app.models.workflow import Workflow as WfModel

    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    async with db_session_context() as session:
        result = await session.execute(
            update(WfModel)
            .where(
                WfModel.status.in_(["pending", "decomposing", "running"]),
                WfModel.created_at < cutoff,
            )
            .values(
                status=WorkflowStatus.FAILED,
                result={"error": "Workflow timed out (stale cleanup)"},
                completed_at=datetime.now(UTC),
            )
            .returning(WfModel.workflow_id)
        )
        cleaned_ids = [str(row[0]) for row in result.fetchall()]

    return {"cleaned": len(cleaned_ids), "workflow_ids": cleaned_ids}


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
        select(ReasoningTrace).where(ReasoningTrace.workflow_id == workflow_id).order_by(ReasoningTrace.created_at)
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
