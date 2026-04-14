from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.llm.router import get_model_router

logger = get_logger(__name__)

GOAL_INTERPRETATION_PROMPT = """Analyze this user request and extract a structured goal.

Request: {prompt}

Respond with ONLY valid JSON:
{{
  "intent": "analyze|create|compare|explain|plan|debug|research|optimize",
  "constraints": ["list of constraints or requirements"],
  "risk_level": "low|medium|high",
  "expected_output_type": "report|data|decision|plan|answer|artifact",
  "complexity_estimate": "simple|moderate|complex",
  "domain_hint": "detected domain or null",
  "stop_conditions": ["conditions that indicate success"]
}}"""


class StructuredGoal(BaseModel):
    """Parsed user intent with metadata for planning."""

    original_prompt: str
    intent: str = "analyze"
    constraints: list[str] = Field(default_factory=list)
    risk_level: str = "medium"  # low, medium, high
    expected_output_type: str = "report"  # report, data, decision, plan, answer, artifact
    complexity_estimate: str = "moderate"  # simple, moderate, complex
    domain_hint: str | None = None
    stop_conditions: list[str] = Field(default_factory=list)


class GoalInterpreter:
    """Parse user prompt into a StructuredGoal via LLM analysis."""

    def __init__(self) -> None:
        self.router = get_model_router()

    async def interpret(self, prompt: str) -> StructuredGoal:
        """Interpret user prompt into structured goal."""
        try:
            response = await self.router.generate(
                prompt=GOAL_INTERPRETATION_PROMPT.format(prompt=prompt),
                model="claude-sonnet-4-20250514",  # Haiku for cheap structured parsing
                max_tokens=300,
                temperature=0.1,
            )

            # Parse JSON from response
            content = response.content.strip()
            # Handle markdown fences
            if "```" in content:
                import re

                match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, re.DOTALL)
                if match:
                    content = match.group(1).strip()

            # Find JSON object
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                content = content[start : end + 1]

            parsed = json.loads(content)

            goal = StructuredGoal(
                original_prompt=prompt,
                intent=parsed.get("intent", "analyze"),
                constraints=parsed.get("constraints", []),
                risk_level=parsed.get("risk_level", "medium"),
                expected_output_type=parsed.get("expected_output_type", "report"),
                complexity_estimate=parsed.get("complexity_estimate", "moderate"),
                domain_hint=parsed.get("domain_hint"),
                stop_conditions=parsed.get("stop_conditions", []),
            )

            logger.info(
                "goal_interpreted",
                intent=goal.intent,
                complexity=goal.complexity_estimate,
                risk=goal.risk_level,
                domain=goal.domain_hint,
            )

            return goal

        except Exception as e:
            logger.warning("goal_interpretation_failed", error=str(e))
            # Fallback: heuristic interpretation
            return self._heuristic_interpret(prompt)

    def _heuristic_interpret(self, prompt: str) -> StructuredGoal:
        """Fallback heuristic interpretation when LLM fails."""
        prompt_lower = prompt.lower()

        # Detect intent
        intent = "analyze"
        if any(w in prompt_lower for w in ["compare", "versus", "vs", "difference"]):
            intent = "compare"
        elif any(w in prompt_lower for w in ["explain", "what is", "how does"]):
            intent = "explain"
        elif any(w in prompt_lower for w in ["create", "build", "generate", "write"]):
            intent = "create"
        elif any(w in prompt_lower for w in ["plan", "strategy", "roadmap"]):
            intent = "plan"
        elif any(w in prompt_lower for w in ["fix", "debug", "error", "issue"]):
            intent = "debug"

        # Detect complexity
        word_count = len(prompt.split())
        complexity = "simple" if word_count < 20 else "moderate" if word_count < 60 else "complex"

        # Detect risk
        risk = "low"
        if any(w in prompt_lower for w in ["critical", "urgent", "production", "patient", "financial", "legal"]):
            risk = "high"
        elif any(w in prompt_lower for w in ["important", "significant", "risk"]):
            risk = "medium"

        return StructuredGoal(
            original_prompt=prompt,
            intent=intent,
            complexity_estimate=complexity,
            risk_level=risk,
            expected_output_type="answer" if complexity == "simple" else "report",
        )
