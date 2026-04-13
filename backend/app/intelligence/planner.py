from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.intelligence.goal_interpreter import StructuredGoal

logger = get_logger(__name__)


class ExecutionMode(str, Enum):
    DIRECT_RESPONSE = "direct_response"
    DAG_PIPELINE = "dag_pipeline"
    ITERATIVE = "iterative"
    VERIFY_AND_REFINE = "verify_and_refine"
    EXPLORE_AND_PRUNE = "explore_and_prune"


class ExecutionPlan(BaseModel):
    """Execution strategy determined by the planner."""
    mode: ExecutionMode
    goal: StructuredGoal
    max_iterations: int = 5
    confidence_threshold: float = 0.8
    budget_allocation: dict[str, float] = Field(default_factory=lambda: {
        "execution": 0.70,
        "reflection": 0.10,
        "verification": 0.10,
        "synthesis": 0.10,
    })
    reasoning: str = ""  # Why this mode was chosen


class AdaptivePlanner:
    """Choose execution mode based on goal analysis. Deterministic rules, no LLM calls."""

    def plan(self, goal: StructuredGoal, budget: float) -> ExecutionPlan:
        """Select execution mode based on goal properties."""

        # Rule 1: Simple questions → direct LLM response (no DAG)
        if goal.complexity_estimate == "simple" and goal.risk_level == "low":
            mode = ExecutionMode.DIRECT_RESPONSE
            reasoning = "Simple, low-risk request → direct LLM response without multi-agent orchestration"
            plan = ExecutionPlan(
                mode=mode, goal=goal, max_iterations=1,
                confidence_threshold=0.5, reasoning=reasoning,
                budget_allocation={"execution": 0.90, "synthesis": 0.10},
            )
            logger.info("plan_selected", mode=mode.value, reasoning=reasoning)
            return plan

        # Rule 2: High risk → verify and refine (DAG + consensus + retry)
        if goal.risk_level == "high":
            mode = ExecutionMode.VERIFY_AND_REFINE
            reasoning = "High-risk request → DAG pipeline with mandatory verification pass"
            plan = ExecutionPlan(
                mode=mode, goal=goal, max_iterations=3,
                confidence_threshold=0.9, reasoning=reasoning,
                budget_allocation={"execution": 0.55, "reflection": 0.10, "verification": 0.25, "synthesis": 0.10},
            )
            logger.info("plan_selected", mode=mode.value, reasoning=reasoning)
            return plan

        # Rule 3: Comparison intent → explore and prune (parallel branches)
        if goal.intent == "compare":
            mode = ExecutionMode.EXPLORE_AND_PRUNE
            reasoning = "Comparison request → explore multiple approaches in parallel, select best"
            plan = ExecutionPlan(
                mode=mode, goal=goal, max_iterations=2,
                confidence_threshold=0.7, reasoning=reasoning,
                budget_allocation={"execution": 0.65, "reflection": 0.15, "verification": 0.10, "synthesis": 0.10},
            )
            logger.info("plan_selected", mode=mode.value, reasoning=reasoning)
            return plan

        # Rule 4: Explain/debug with moderate complexity → iterative refinement
        if goal.intent in ("explain", "debug") and goal.complexity_estimate == "moderate":
            mode = ExecutionMode.ITERATIVE
            reasoning = f"{goal.intent} with moderate complexity → iterative agent-reflect loop"
            plan = ExecutionPlan(
                mode=mode, goal=goal, max_iterations=5,
                confidence_threshold=0.8, reasoning=reasoning,
                budget_allocation={"execution": 0.60, "reflection": 0.20, "verification": 0.05, "synthesis": 0.15},
            )
            logger.info("plan_selected", mode=mode.value, reasoning=reasoning)
            return plan

        # Rule 5: Default → classic DAG pipeline (backward compatible)
        mode = ExecutionMode.DAG_PIPELINE
        reasoning = "Standard request → classic DAG decomposition and parallel execution"
        plan = ExecutionPlan(
            mode=mode, goal=goal, max_iterations=1,
            confidence_threshold=0.7, reasoning=reasoning,
        )
        logger.info("plan_selected", mode=mode.value, reasoning=reasoning)
        return plan
