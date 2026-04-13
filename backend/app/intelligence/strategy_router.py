from __future__ import annotations

import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.intelligence.guardrails import ExecutionGuardrails
from app.intelligence.planner import ExecutionMode, ExecutionPlan
from app.intelligence.reflection import ReflectionEngine
from app.llm.router import get_model_router
from app.models.workflow import Workflow

logger = get_logger(__name__)


class StrategyRouter:
    """Route execution plans to the appropriate strategy implementation."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis
        self.guardrails = ExecutionGuardrails()
        self.reflection = ReflectionEngine()
        self.router = get_model_router()

    async def execute(self, plan: ExecutionPlan, workflow: Workflow) -> dict:
        """Execute workflow using the planned strategy."""
        workflow.execution_mode = plan.mode.value
        workflow.goal_snapshot = plan.goal.model_dump()
        await self.session.flush()

        logger.info(
            "strategy_executing",
            mode=plan.mode.value,
            workflow_id=str(workflow.workflow_id),
        )

        match plan.mode:
            case ExecutionMode.DIRECT_RESPONSE:
                return await self._direct_response(plan, workflow)
            case ExecutionMode.DAG_PIPELINE:
                return await self._dag_pipeline(plan, workflow)
            case ExecutionMode.ITERATIVE:
                return await self._iterative(plan, workflow)
            case ExecutionMode.VERIFY_AND_REFINE:
                return await self._verify_and_refine(plan, workflow)
            case ExecutionMode.EXPLORE_AND_PRUNE:
                return await self._explore_and_prune(plan, workflow)
            case _:
                return await self._dag_pipeline(plan, workflow)

    async def _direct_response(self, plan: ExecutionPlan, workflow: Workflow) -> dict:
        """Single LLM call for simple requests. No agents, no DAG."""
        from app.models.base import WorkflowStatus

        workflow.status = WorkflowStatus.RUNNING
        workflow.started_at = datetime.now(timezone.utc)
        await self.session.flush()

        response = await self.router.generate(
            prompt=plan.goal.original_prompt,
            max_tokens=4096,
            temperature=0.7,
        )

        result = {
            "content": response.content,
            "confidence": 0.85,
            "summary": "Direct response (no multi-agent orchestration needed)",
            "execution_mode": "direct_response",
            "synthesis_cost": str(response.cost),
            "synthesis_tokens": response.total_tokens,
        }

        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.now(timezone.utc)
        workflow.result = result
        await self.session.flush()

        return result

    async def _dag_pipeline(self, plan: ExecutionPlan, workflow: Workflow) -> dict:
        """Classic DAG decomposition + parallel execution. Delegates to existing orchestration."""
        from sqlalchemy import select
        from app.models.agent import Agent
        from app.models.base import WorkflowStatus
        from app.orchestration.decomposer import TaskDecomposer
        from app.orchestration.scheduler import WorkflowScheduler
        from app.orchestration.selector import AgentSelector

        workflow.status = WorkflowStatus.DECOMPOSING
        await self.session.flush()

        # Get available capabilities
        result = await self.session.execute(
            select(Agent.capabilities).where(Agent.status.in_(["active", "degraded"]))
        )
        all_caps: set[str] = set()
        for row in result.all():
            caps = row[0]
            if isinstance(caps, list):
                all_caps.update(caps)

        # Decompose
        decomposer = TaskDecomposer()
        dag, detected_domain = await decomposer.decompose(
            prompt=plan.goal.original_prompt,
            available_capabilities=sorted(all_caps),
            domain_hint=plan.goal.domain_hint,
        )

        workflow.domain = detected_domain
        workflow.dag_snapshot = dag.to_dict()
        await self.session.flush()

        # Assign agents
        selector = AgentSelector(self.session)
        agents = await selector.select_agents_for_dag(dag, float(workflow.budget_limit))

        # Execute
        scheduler = WorkflowScheduler(self.session, self.redis)
        return await scheduler.execute_workflow(workflow, dag, agents)

    async def _iterative(self, plan: ExecutionPlan, workflow: Workflow) -> dict:
        """Iterative agent-reflect loop until confident or budget exhausted."""
        from app.models.base import WorkflowStatus

        workflow.status = WorkflowStatus.RUNNING
        workflow.started_at = datetime.now(timezone.utc)
        await self.session.flush()

        outputs: list[dict[str, str]] = []
        iteration = 0
        reflection_count = 0
        total_tokens = 0

        while iteration < plan.max_iterations:
            iteration += 1

            # Generate response
            context = "\n\n".join(o.get("content", "") for o in outputs) if outputs else "No prior context."
            prompt = (
                f"## Task (iteration {iteration})\n{plan.goal.original_prompt}\n\n"
                f"## Prior Context\n{context}\n\n"
                f"## Instructions\nBuild on prior context. Be thorough and specific."
            )

            response = await self.router.generate(prompt=prompt, max_tokens=4096, temperature=0.5)
            total_tokens += response.total_tokens
            outputs.append({"capability": f"iteration_{iteration}", "content": response.content})

            # Reflect
            reflection = await self.reflection.reflect(plan.goal.original_prompt, outputs)
            reflection_count += 1

            if reflection.action == "accept" or reflection.confidence >= plan.confidence_threshold:
                break

            # Check guardrails
            should_stop, reason = self.guardrails.should_force_stop(
                iteration, reflection_count, 0, float(workflow.budget_limit), 0
            )
            if should_stop:
                logger.warning("iterative_force_stopped", reason=reason)
                break

        # Compose final result
        final_content = outputs[-1]["content"] if outputs else "No output produced."
        result = {
            "content": final_content,
            "confidence": min(0.95, 0.5 + iteration * 0.1),
            "summary": f"Iterative refinement: {iteration} iterations, {reflection_count} reflections",
            "execution_mode": "iterative",
            "iterations": iteration,
            "synthesis_tokens": total_tokens,
        }

        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.now(timezone.utc)
        workflow.result = result
        await self.session.flush()

        return result

    async def _verify_and_refine(self, plan: ExecutionPlan, workflow: Workflow) -> dict:
        """DAG pipeline + verification pass + optional retry."""
        # First run the DAG pipeline
        dag_result = await self._dag_pipeline(plan, workflow)

        # Then reflect on the result
        outputs = [{"capability": "dag_pipeline", "content": dag_result.get("content", "")}]
        reflection = await self.reflection.reflect(plan.goal.original_prompt, outputs)

        # If reflection says accept, we're done
        if reflection.action == "accept" and reflection.confidence >= plan.confidence_threshold:
            dag_result["execution_mode"] = "verify_and_refine"
            dag_result["verification"] = {"status": "accepted", "confidence": reflection.confidence}
            return dag_result

        # Otherwise add verification note
        dag_result["execution_mode"] = "verify_and_refine"
        dag_result["verification"] = {
            "status": reflection.action,
            "confidence": reflection.confidence,
            "issues": reflection.issues,
            "suggestions": reflection.suggestions,
        }
        return dag_result

    async def _explore_and_prune(self, plan: ExecutionPlan, workflow: Workflow) -> dict:
        """Run DAG pipeline and select best result (simplified: single run with reflection)."""
        # For now, this is equivalent to verify_and_refine
        # Full implementation would spawn parallel branches with different decompositions
        return await self._verify_and_refine(plan, workflow)
