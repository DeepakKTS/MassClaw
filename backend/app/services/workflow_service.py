from __future__ import annotations

import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import NotFoundError, OrchestrationError
from app.models.base import TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowResponse,
    WorkflowResultResponse,
    WorkflowStatusResponse,
)

logger = get_logger(__name__)


class WorkflowService:
    """Service layer for workflow lifecycle management."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def create_and_execute(self, data: WorkflowCreate) -> Workflow:
        """Create a workflow and execute it via the intelligent strategy router.

        Flow: Create → Interpret Goal → Plan Strategy → Execute
        When the planner selects DAG_PIPELINE, behavior is identical to v1.
        """
        # 1. Create workflow
        workflow = Workflow(
            user_id=data.user_id,
            prompt=data.prompt,
            domain=data.domain,
            status=WorkflowStatus.PENDING,
            budget_limit=data.budget_limit,
            priority=data.priority,
            metadata_=data.metadata,
        )
        self.session.add(workflow)
        await self.session.flush()
        await self.session.refresh(workflow)

        logger.info(
            "workflow_created",
            workflow_id=str(workflow.workflow_id),
            budget=data.budget_limit,
        )

        try:
            # 2. Interpret goal
            from app.intelligence.goal_interpreter import GoalInterpreter
            from app.intelligence.planner import AdaptivePlanner
            from app.intelligence.strategy_router import StrategyRouter

            goal = await GoalInterpreter().interpret(data.prompt)

            # 3. Plan execution strategy
            plan = AdaptivePlanner().plan(goal, float(data.budget_limit))

            logger.info(
                "workflow_planned",
                workflow_id=str(workflow.workflow_id),
                mode=plan.mode.value,
                reasoning=plan.reasoning[:100],
            )

            # 4. Execute via strategy router
            router = StrategyRouter(self.session, self.redis)
            await router.execute(plan, workflow)

            return workflow

        except Exception as e:
            workflow.status = WorkflowStatus.FAILED
            workflow.completed_at = datetime.now(UTC)
            workflow.result = {"error": str(e)}
            await self.session.flush()
            logger.error("workflow_failed", workflow_id=str(workflow.workflow_id), error=str(e))
            raise

    async def get_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        """Get a workflow by ID."""
        result = await self.session.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise NotFoundError("Workflow", str(workflow_id))
        return workflow

    async def get_workflow_status(self, workflow_id: uuid.UUID) -> WorkflowStatusResponse:
        """Get workflow status with progress information."""
        workflow = await self.get_workflow(workflow_id)

        # Count tasks by status
        task_counts = await self.session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Task.status == TaskStatus.COMPLETED).label("completed"),
                func.count().filter(Task.status == TaskStatus.RUNNING).label("running"),
                func.count().filter(Task.status == TaskStatus.FAILED).label("failed"),
            ).where(Task.workflow_id == workflow_id)
        )
        counts = task_counts.one()

        total = counts.total or 0
        completed = counts.completed or 0
        progress = (completed / total * 100) if total > 0 else 0

        elapsed = None
        if workflow.started_at:
            end = workflow.completed_at or datetime.now(UTC)
            if workflow.started_at.tzinfo is None:
                started = workflow.started_at.replace(tzinfo=UTC)
            else:
                started = workflow.started_at
            elapsed = (end - started).total_seconds()

        return WorkflowStatusResponse(
            workflow_id=workflow.workflow_id,
            status=workflow.status,
            progress_percent=round(progress, 1),
            total_tasks=total,
            completed_tasks=completed,
            running_tasks=counts.running or 0,
            failed_tasks=counts.failed or 0,
            budget_used=float(workflow.budget_used),
            budget_limit=float(workflow.budget_limit),
            started_at=workflow.started_at,
            elapsed_seconds=round(elapsed, 1) if elapsed else None,
        )

    async def get_workflow_result(self, workflow_id: uuid.UUID) -> WorkflowResultResponse:
        """Get the final synthesized result of a completed workflow."""
        workflow = await self.get_workflow(workflow_id)

        # Count tasks
        task_counts = await self.session.execute(
            select(
                func.count().filter(Task.status == TaskStatus.COMPLETED).label("completed"),
                func.count().filter(Task.status == TaskStatus.FAILED).label("failed"),
                func.coalesce(func.sum(Task.cost_used), 0).label("total_cost"),
                func.coalesce(func.sum(Task.latency_ms), 0).label("total_latency"),
            ).where(Task.workflow_id == workflow_id)
        )
        counts = task_counts.one()

        confidence = None
        if workflow.result and isinstance(workflow.result, dict):
            confidence = workflow.result.get("confidence")

        return WorkflowResultResponse(
            workflow_id=workflow.workflow_id,
            status=workflow.status,
            result=workflow.result,
            confidence=confidence,
            total_cost=float(counts.total_cost),
            total_latency_ms=float(counts.total_latency),
            tasks_completed=counts.completed or 0,
            tasks_failed=counts.failed or 0,
            completed_at=workflow.completed_at,
        )

    async def cancel_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        """Cancel a running workflow."""
        workflow = await self.get_workflow(workflow_id)
        if workflow.status not in (WorkflowStatus.PENDING, WorkflowStatus.DECOMPOSING, WorkflowStatus.RUNNING):
            raise OrchestrationError(f"Cannot cancel workflow in status {workflow.status.value}")

        workflow.status = WorkflowStatus.CANCELLED
        workflow.completed_at = datetime.now(UTC)
        await self.session.flush()

        logger.info("workflow_cancelled", workflow_id=str(workflow_id))
        return workflow

    async def list_workflows(
        self,
        pagination: PaginationParams,
        status: WorkflowStatus | None = None,
    ) -> PaginatedResponse[WorkflowResponse]:
        """List workflows with pagination."""
        query = select(Workflow)
        count_query = select(func.count()).select_from(Workflow)

        if status:
            query = query.where(Workflow.status == status)
            count_query = count_query.where(Workflow.status == status)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        result = await self.session.execute(
            query.order_by(Workflow.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
        )
        workflows = list(result.scalars().all())

        return PaginatedResponse(
            items=[WorkflowResponse.model_validate(w) for w in workflows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
