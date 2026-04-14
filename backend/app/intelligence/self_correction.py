from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_RETRIES_PER_TASK = 2


@dataclass
class RetryDecision:
    should_retry: bool
    improved_prompt: str | None = None
    reason: str = ""


class SelfCorrectionEngine:
    """Decides whether to retry a task and builds improved prompts with feedback."""

    def evaluate(
        self,
        task_output: str,
        reflection_action: str,
        reflection_confidence: float,
        reflection_issues: list[str],
        reflection_suggestions: list[str],
        retry_count: int,
    ) -> RetryDecision:
        """Decide whether to retry based on reflection results."""
        if retry_count >= MAX_RETRIES_PER_TASK:
            return RetryDecision(should_retry=False, reason=f"Max retries ({MAX_RETRIES_PER_TASK}) exceeded")

        if reflection_action not in ("retry_task", "re_plan"):
            return RetryDecision(should_retry=False, reason=f"Reflection action is '{reflection_action}', not retry")

        if reflection_confidence >= 0.6:
            return RetryDecision(should_retry=False, reason=f"Confidence {reflection_confidence:.2f} is acceptable")

        return RetryDecision(
            should_retry=True,
            improved_prompt=self._build_feedback_prompt(reflection_issues, reflection_suggestions),
            reason=f"Low confidence ({reflection_confidence:.2f}) with issues: {', '.join(reflection_issues[:2])}",
        )

    @staticmethod
    def _build_feedback_prompt(issues: list[str], suggestions: list[str]) -> str:
        """Build a feedback section to append to the retry prompt."""
        parts = ["\n\n## Feedback from Quality Review\n"]
        if issues:
            parts.append("**Issues identified:**")
            for issue in issues[:3]:
                parts.append(f"- {issue}")
        if suggestions:
            parts.append("\n**Suggestions for improvement:**")
            for suggestion in suggestions[:3]:
                parts.append(f"- {suggestion}")
        parts.append("\nPlease address these issues in your revised response.")
        return "\n".join(parts)
