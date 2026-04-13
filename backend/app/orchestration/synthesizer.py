from __future__ import annotations

from app.core.logging import get_logger
from app.llm.base import LLMResponse
from app.llm.router import get_model_router
from app.orchestration.dag import DAG

logger = get_logger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are MassClaw's output synthesizer. Given the results from multiple specialized AI agents that each worked on a subtask of a larger request, produce a unified, coherent final response.

## Rules
1. Integrate findings from ALL agents into a single, well-structured response.
2. Resolve any contradictions by favoring higher-confidence findings.
3. If any tasks failed or were skipped, note what's missing and caveat affected conclusions.
4. Structure the output with clear sections matching the original request's scope.
5. Be comprehensive but concise — no redundancy between sections.
6. Use markdown formatting for readability."""

SYNTHESIS_USER_PROMPT = """## Original Request
{original_prompt}

## Agent Outputs
{agent_outputs}

## Task Summary
- Total tasks: {total_tasks}
- Completed: {completed_tasks}
- Failed: {failed_tasks}
- Skipped: {skipped_tasks}

Synthesize all completed agent outputs into a single coherent response addressing the original request."""


class OutputSynthesizer:
    """Synthesizes outputs from multiple agent tasks into a final coherent response."""

    def __init__(self) -> None:
        self.router = get_model_router()

    async def synthesize(
        self,
        dag: DAG,
        original_prompt: str,
    ) -> dict:
        """Synthesize all completed task outputs into a final response.

        Returns dict with 'content', 'confidence', 'summary'.
        """
        # Gather outputs from completed nodes
        outputs_parts: list[str] = []
        for node in dag.topological_sort():
            if node.output:
                outputs_parts.append(
                    f"### {node.capability} (Step {node.node_id})\n{node.output}"
                )

        if not outputs_parts:
            return {
                "content": "No agent outputs were produced. All tasks may have failed.",
                "confidence": 0.0,
                "summary": "Workflow produced no results.",
            }

        agent_outputs_text = "\n\n".join(outputs_parts)

        completed = dag.completed_count
        failed = dag.failed_count
        total = len(dag.nodes)
        skipped = sum(1 for n in dag.nodes if n.status.value == "skipped")

        user_msg = SYNTHESIS_USER_PROMPT.format(
            original_prompt=original_prompt,
            agent_outputs=agent_outputs_text,
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            skipped_tasks=skipped,
        )

        response: LLMResponse = await self.router.generate(
            prompt=user_msg,
            system=SYNTHESIS_SYSTEM_PROMPT,
            model="claude-sonnet-4-20250514",  # Sonnet for high-quality synthesis
            max_tokens=3000,
            temperature=0.5,
        )

        # Confidence based on task completion rate
        confidence = completed / total if total > 0 else 0.0

        logger.info(
            "synthesis_complete",
            total_tasks=total,
            completed=completed,
            failed=failed,
            confidence=round(confidence, 2),
            output_tokens=response.output_tokens,
        )

        return {
            "content": response.content,
            "confidence": round(confidence, 2),
            "summary": f"Synthesized from {completed}/{total} completed tasks.",
            "synthesis_cost": str(response.cost),
            "synthesis_tokens": response.total_tokens,
        }
