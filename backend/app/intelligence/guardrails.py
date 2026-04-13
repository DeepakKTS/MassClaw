from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionGuardrails:
    """Safety guardrails for intelligent execution. Pure validation, no LLM calls."""

    max_iterations: int = 10
    max_reflection_calls: int = 5
    max_branch_count: int = 3
    budget_hard_ceiling_percent: float = 100.0
    budget_reflection_ceiling_percent: float = 10.0
    min_confidence_to_accept: float = 0.7
    max_retry_per_task: int = 3
    max_total_tasks: int = 20
    max_execution_time_seconds: float = 300.0  # 5 minutes

    def check_iteration_limit(self, current: int) -> bool:
        """Returns True if iteration is within limits."""
        return current < self.max_iterations

    def check_reflection_limit(self, current: int) -> bool:
        return current < self.max_reflection_calls

    def check_branch_limit(self, current: int) -> bool:
        return current < self.max_branch_count

    def check_budget(self, used: float, limit: float) -> bool:
        """Returns True if within budget ceiling."""
        if limit <= 0:
            return False
        usage_pct = (used / limit) * 100
        return usage_pct < self.budget_hard_ceiling_percent

    def check_reflection_budget(self, reflection_cost: float, total_budget: float) -> bool:
        """Returns True if reflection cost is within its budget ceiling."""
        if total_budget <= 0:
            return False
        reflection_pct = (reflection_cost / total_budget) * 100
        return reflection_pct < self.budget_reflection_ceiling_percent

    def check_task_count(self, current: int) -> bool:
        return current < self.max_total_tasks

    def should_force_stop(
        self,
        iteration: int,
        reflection_count: int,
        budget_used: float,
        budget_limit: float,
        elapsed_seconds: float,
    ) -> tuple[bool, str]:
        """Check if execution should be forcibly stopped. Returns (should_stop, reason)."""
        if not self.check_iteration_limit(iteration):
            return True, f"Max iterations ({self.max_iterations}) exceeded"
        if not self.check_reflection_limit(reflection_count):
            return True, f"Max reflection calls ({self.max_reflection_calls}) exceeded"
        if not self.check_budget(budget_used, budget_limit):
            return True, f"Budget ceiling ({self.budget_hard_ceiling_percent}%) exceeded"
        if elapsed_seconds > self.max_execution_time_seconds:
            return True, f"Max execution time ({self.max_execution_time_seconds}s) exceeded"
        return False, ""
