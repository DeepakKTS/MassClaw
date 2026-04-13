from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.logging import get_logger
from app.exceptions import OrchestrationError
from app.llm.router import get_model_router
from app.orchestration.dag import DAG, DAGNode
from app.orchestration.prompts.decompose import (
    DECOMPOSITION_RETRY_PROMPT,
    DECOMPOSITION_SYSTEM_PROMPT,
    DECOMPOSITION_USER_PROMPT,
)

logger = get_logger(__name__)

MAX_DECOMPOSITION_RETRIES = 2


# --- Validation schema for LLM output ---


class DecomposedTask(BaseModel):
    id: str
    capability: str
    description: str = Field(min_length=10)
    depends_on: list[str] = Field(default_factory=list)
    estimated_complexity: str = "medium"

    @field_validator("estimated_complexity")
    @classmethod
    def validate_complexity(cls, v: str) -> str:
        if v not in ("low", "medium", "high"):
            return "medium"
        return v


class DecompositionResult(BaseModel):
    domain: str = ""
    tasks: list[DecomposedTask] = Field(min_length=1)


class TaskDecomposer:
    """LLM-powered task decomposition engine.

    Breaks a user prompt into a validated DAG of atomic subtasks.
    Uses structured JSON output with Pydantic validation and retry on failure.
    """

    def __init__(self) -> None:
        self.router = get_model_router()

    async def decompose(
        self,
        prompt: str,
        available_capabilities: list[str],
        domain_hint: str | None = None,
    ) -> tuple[DAG, str]:
        """Decompose a user prompt into a validated task DAG.

        Args:
            prompt: The user's request to decompose.
            available_capabilities: List of capabilities available in the agent registry.
            domain_hint: Optional domain hint to guide decomposition.

        Returns:
            Tuple of (DAG, detected_domain).

        Raises:
            OrchestrationError: If decomposition fails after retries.
        """
        caps_str = ", ".join(sorted(available_capabilities))

        system = DECOMPOSITION_SYSTEM_PROMPT.format(available_capabilities=caps_str)
        user_msg = DECOMPOSITION_USER_PROMPT.format(user_prompt=prompt)

        last_error = ""
        for attempt in range(1 + MAX_DECOMPOSITION_RETRIES):
            try:
                if attempt == 0:
                    msg = user_msg
                else:
                    msg = DECOMPOSITION_RETRY_PROMPT.format(
                        error=last_error,
                        available_capabilities=caps_str,
                        user_prompt=prompt,
                    )

                response = await self.router.generate(
                    prompt=msg,
                    system=system,
                    max_tokens=4096,
                    temperature=0.3,  # Low temperature for structured output
                )

                # Parse JSON from response
                parsed = self._extract_json(response.content)
                result = DecompositionResult.model_validate(parsed)

                # Validate capabilities exist
                for task in result.tasks:
                    if task.capability not in available_capabilities:
                        # Try fuzzy match
                        matched = self._fuzzy_match_capability(
                            task.capability, available_capabilities
                        )
                        if matched:
                            task.capability = matched
                        else:
                            raise ValueError(
                                f"Task '{task.id}' uses unknown capability '{task.capability}'"
                            )

                # Build DAG
                nodes = [
                    DAGNode(
                        node_id=t.id,
                        capability=t.capability,
                        description=t.description,
                        depends_on=t.depends_on,
                        estimated_complexity=t.estimated_complexity,
                    )
                    for t in result.tasks
                ]
                dag = DAG(nodes)  # This validates (no cycles, valid refs)

                domain = result.domain or domain_hint or "general"

                logger.info(
                    "decomposition_complete",
                    domain=domain,
                    task_count=len(result.tasks),
                    attempt=attempt + 1,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )

                return dag, domain

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "decomposition_attempt_failed",
                    attempt=attempt + 1,
                    error=last_error,
                )

        raise OrchestrationError(
            f"Task decomposition failed after {1 + MAX_DECOMPOSITION_RETRIES} attempts: {last_error}"
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown fences and preamble."""
        # Try direct parse first
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip markdown code fences
        pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Find first { and last }
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(text[first_brace : last_brace + 1])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract valid JSON from LLM response: {text[:200]}...")

    @staticmethod
    def _fuzzy_match_capability(target: str, available: list[str]) -> str | None:
        """Try to match a capability by partial/fuzzy matching."""
        target_lower = target.lower().replace("_", "-").replace(" ", "-")

        # Exact match
        if target_lower in available:
            return target_lower

        # Substring match
        for cap in available:
            if target_lower in cap or cap in target_lower:
                return cap

        # Word overlap match
        target_words = set(target_lower.split("-"))
        best_match = None
        best_overlap = 0
        for cap in available:
            cap_words = set(cap.split("-"))
            overlap = len(target_words & cap_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = cap

        return best_match if best_overlap > 0 else None
