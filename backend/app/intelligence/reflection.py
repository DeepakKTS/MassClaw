from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.llm.router import get_model_router

logger = get_logger(__name__)

REFLECTION_PROMPT = """You are a quality evaluator for an AI agent orchestration system.

## Original Goal
{goal}

## Completed Task Outputs So Far
{outputs}

## Question
Evaluate the outputs and decide the next action.

Respond with ONLY valid JSON:
{{
  "should_continue": true/false,
  "confidence": 0.0-1.0,
  "issues": ["list of issues found, if any"],
  "suggestions": ["list of improvements, if any"],
  "action": "accept|retry_task|add_verifier|re_plan|abort"
}}

Rules:
- "accept" if outputs are sufficient and high quality
- "retry_task" if a specific output is weak
- "add_verifier" if outputs need cross-checking
- "re_plan" if the approach needs fundamental change
- "abort" only if the goal is unachievable"""


class ReflectionResult(BaseModel):
    should_continue: bool = True
    confidence: float = 0.5
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    action: str = "accept"  # accept, retry_task, add_verifier, re_plan, abort


class ReflectionEngine:
    """Evaluate intermediate results and decide next action."""

    def __init__(self) -> None:
        self.router = get_model_router()

    async def reflect(
        self,
        goal_description: str,
        completed_outputs: list[dict[str, str]],
    ) -> ReflectionResult:
        """Evaluate outputs against the goal and recommend next action."""
        if not completed_outputs:
            return ReflectionResult(
                should_continue=True, confidence=0.0,
                action="accept", issues=["No outputs to evaluate"],
            )

        outputs_text = "\n\n".join(
            f"### {o.get('capability', 'unknown')}\n{o.get('content', '')[:1000]}"
            for o in completed_outputs
        )

        try:
            import json
            response = await self.router.generate(
                prompt=REFLECTION_PROMPT.format(goal=goal_description, outputs=outputs_text),
                model="claude-sonnet-4-20250514",  # Haiku for cheap reflection
                max_tokens=300,
                temperature=0.1,
            )

            content = response.content.strip()
            # Extract JSON
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(content[start:end + 1])
                result = ReflectionResult(
                    should_continue=parsed.get("should_continue", True),
                    confidence=parsed.get("confidence", 0.5),
                    issues=parsed.get("issues", []),
                    suggestions=parsed.get("suggestions", []),
                    action=parsed.get("action", "accept"),
                )
                logger.info(
                    "reflection_complete",
                    action=result.action,
                    confidence=result.confidence,
                    issues_count=len(result.issues),
                )
                return result

        except Exception as e:
            logger.warning("reflection_failed", error=str(e))

        # Fallback: accept if we have outputs
        return ReflectionResult(
            should_continue=False, confidence=0.7,
            action="accept", issues=[], suggestions=["Reflection LLM call failed, accepting outputs"],
        )
