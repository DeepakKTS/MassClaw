from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.llm.router import get_model_router
from app.models.agent import Agent
from app.models.base import AgentStatus

logger = get_logger(__name__)

VERIFICATION_PROMPT = """You are a verification agent. Evaluate the following output against the original task.

## Original Task
{task_description}

## Output to Verify
{output}

## Instructions
1. Is the output factually consistent and logically sound?
2. Does it adequately address the task requirements?
3. Are there any contradictions, unsupported claims, or gaps?

Respond with JSON:
{{"agree": true/false, "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""


@dataclass
class ConsensusResult:
    agreed: bool
    weighted_agreement: float
    verifier_count: int
    details: list[dict]


class ConsensusService:
    """Multi-agent consensus verification for high-stakes tasks.

    Selects N verifier agents, asks each to independently evaluate a result,
    then aggregates via trust-weighted voting.
    """

    CONSENSUS_THRESHOLD = 0.6

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.router = get_model_router()

    async def verify_with_consensus(
        self,
        task_description: str,
        output: str,
        verifier_count: int = 3,
        exclude_agent_id: uuid.UUID | None = None,
    ) -> ConsensusResult:
        """Run consensus verification on a task output.

        Returns ConsensusResult with weighted agreement score.
        """
        # Select verifier agents
        query = (
            select(Agent)
            .where(
                Agent.status == AgentStatus.ACTIVE,
                Agent.capabilities.op("@>")(["verification"]),
            )
            .order_by(Agent.trust_score.desc())
            .limit(verifier_count)
        )
        result = await self.session.execute(query)
        verifiers = list(result.scalars().all())

        if not verifiers:
            logger.warning("no_verifier_agents_available")
            return ConsensusResult(
                agreed=True, weighted_agreement=1.0, verifier_count=0, details=[]
            )

        # Ask each verifier to evaluate independently
        details = []
        total_weight = 0.0
        weighted_agree = 0.0

        for verifier in verifiers:
            try:
                prompt = VERIFICATION_PROMPT.format(
                    task_description=task_description,
                    output=output[:3000],  # Truncate to avoid token explosion
                )
                response = await self.router.generate(
                    prompt=prompt,
                    max_tokens=500,
                    temperature=0.2,
                )

                # Parse JSON response
                import json
                try:
                    verdict = json.loads(response.content)
                except json.JSONDecodeError:
                    verdict = {"agree": True, "confidence": 0.5, "reasoning": "Parse error"}

                weight = verifier.trust_score * verdict.get("confidence", 0.5)
                total_weight += weight
                if verdict.get("agree", True):
                    weighted_agree += weight

                details.append({
                    "agent_id": str(verifier.agent_id),
                    "agent_name": verifier.name,
                    "agree": verdict.get("agree", True),
                    "confidence": verdict.get("confidence", 0.5),
                    "reasoning": verdict.get("reasoning", ""),
                    "weight": round(weight, 4),
                })

            except Exception as e:
                logger.warning("verifier_failed", agent=verifier.name, error=str(e))

        # Compute consensus
        agreement_ratio = weighted_agree / total_weight if total_weight > 0 else 1.0
        agreed = agreement_ratio >= self.CONSENSUS_THRESHOLD

        logger.info(
            "consensus_result",
            agreed=agreed,
            agreement_ratio=round(agreement_ratio, 4),
            verifiers=len(details),
        )

        return ConsensusResult(
            agreed=agreed,
            weighted_agreement=round(agreement_ratio, 4),
            verifier_count=len(details),
            details=details,
        )
