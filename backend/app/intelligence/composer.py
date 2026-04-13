from __future__ import annotations

from app.core.logging import get_logger
from app.llm.router import get_model_router

logger = get_logger(__name__)

COMPOSER_PROMPT = """You are composing the final response for an AI agent orchestration system.

## Original Request
{prompt}

## Execution Mode
{mode}

## Evidence (Agent Outputs)
{evidence}

## Instructions
- Compose a clear, comprehensive response that directly addresses the original request
- Cite which evidence supports each major conclusion
- If any evidence was weak or conflicting, note the uncertainty
- Structure with clear headers and sections
- Be specific and actionable"""


class FinalAnswerComposer:
    """Evidence-based final answer synthesis with traceability."""

    def __init__(self) -> None:
        self.router = get_model_router()

    async def compose(
        self,
        prompt: str,
        execution_mode: str,
        evidence: list[dict[str, str]],
    ) -> dict:
        """Compose final answer from collected evidence."""
        if not evidence:
            return {
                "content": "No evidence was collected during execution.",
                "confidence": 0.0,
                "summary": "Empty result — no agent outputs.",
            }

        evidence_text = "\n\n".join(
            f"### {e.get('capability', 'unknown')} (confidence: {e.get('confidence', 'N/A')})\n{e.get('content', '')}"
            for e in evidence
        )

        response = await self.router.generate(
            prompt=COMPOSER_PROMPT.format(
                prompt=prompt,
                mode=execution_mode,
                evidence=evidence_text,
            ),
            max_tokens=8192,
            temperature=0.5,
        )

        confidence = min(1.0, len(evidence) / 5 * 0.8)  # More evidence = higher confidence

        logger.info(
            "answer_composed",
            mode=execution_mode,
            evidence_count=len(evidence),
            output_tokens=response.output_tokens,
        )

        return {
            "content": response.content,
            "confidence": round(confidence, 2),
            "summary": f"Composed from {len(evidence)} evidence sources ({execution_mode})",
            "execution_mode": execution_mode,
            "synthesis_cost": str(response.cost),
            "synthesis_tokens": response.total_tokens,
        }
