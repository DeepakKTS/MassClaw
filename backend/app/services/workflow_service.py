from __future__ import annotations

import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import NotFoundError, OrchestrationError
from app.models.agent import Agent
from app.models.base import TaskStatus, WorkflowStatus
from app.models.task import Task
from app.models.workflow import Workflow
from app.orchestration.decomposer import TaskDecomposer
from app.orchestration.scheduler import WorkflowScheduler
from app.orchestration.selector import AgentSelector
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
        """Create a workflow, decompose the prompt, and execute it.

        This is the main entry point that drives the entire orchestration pipeline:
        1. Create workflow record
        2. Discover available agent capabilities
        3. Decompose prompt into DAG
        4. Assign agents to DAG nodes
        5. Execute the DAG
        """
        # 1. Create workflow
        workflow = Workflow(
            user_id=data.user_id,
            prompt=data.prompt,
            domain=data.domain,
            status=WorkflowStatus.DECOMPOSING,
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
            # 2. Get available capabilities from registry
            result = await self.session.execute(
                select(Agent.capabilities)
                .where(Agent.status.in_(["active", "degraded"]))
            )
            all_caps: set[str] = set()
            for row in result.all():
                caps = row[0]
                if isinstance(caps, list):
                    all_caps.update(caps)

            if not all_caps:
                raise OrchestrationError("No agents available in the registry")

            # 3. Decompose prompt into DAG
            decomposer = TaskDecomposer()
            dag, detected_domain = await decomposer.decompose(
                prompt=data.prompt,
                available_capabilities=sorted(all_caps),
                domain_hint=data.domain,
            )

            workflow.domain = detected_domain
            workflow.dag_snapshot = dag.to_dict()
            await self.session.flush()

            logger.info(
                "workflow_decomposed",
                workflow_id=str(workflow.workflow_id),
                domain=detected_domain,
                tasks=len(dag.nodes),
            )

            # 4. Assign agents
            selector = AgentSelector(self.session)
            agents = await selector.select_agents_for_dag(dag, float(data.budget_limit))

            # 5. Execute
            scheduler = WorkflowScheduler(self.session, self.redis)
            result = await scheduler.execute_workflow(workflow, dag, agents)

            return workflow

        except Exception as e:
            workflow.status = WorkflowStatus.FAILED
            workflow.completed_at = datetime.now(timezone.utc)
            workflow.result = {"error": str(e)}
            await self.session.flush()
            logger.error("workflow_failed", workflow_id=str(workflow.workflow_id), error=str(e))
            raise

    async def get_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        """Get a workflow by ID."""
        result = await self.session.execute(
            select(Workflow).where(Workflow.workflow_id == workflow_id)
        )
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
            end = workflow.completed_at or datetime.now(timezone.utc)
            if workflow.started_at.tzinfo is None:
                started = workflow.started_at.replace(tzinfo=timezone.utc)
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
        workflow.completed_at = datetime.now(timezone.utc)
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
            query.order_by(Workflow.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        workflows = list(result.scalars().all())

        return PaginatedResponse(
            items=[WorkflowResponse.model_validate(w) for w in workflows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
