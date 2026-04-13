from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.exceptions import AgentUnavailableError
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.orchestration.dag import DAG, DAGNode

logger = get_logger(__name__)


class AgentSelector:
    """Multi-factor agent selection for task assignment.

    Scoring formula:
        score = w_trust * trust + w_cost * cost_eff + w_latency * latency_score
                + w_capability * match_score + w_health * health_score

    Constraint: total estimated cost must not exceed remaining budget.
    """

    # Scoring weights
    W_TRUST = 0.35
    W_COST = 0.20
    W_LATENCY = 0.15
    W_CAPABILITY = 0.15
    W_HEALTH = 0.15

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def select_agent(
        self,
        capability: str,
        budget_remaining: float | None = None,
        exclude_agent_ids: list[uuid.UUID] | None = None,
    ) -> Agent:
        """Select the best agent for a given capability.

        Raises AgentUnavailableError if no suitable agent is found.
        """
        # Query candidates
        query = (
            select(Agent)
            .where(
                Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.DEGRADED]),
            )
        )
        result = await self.session.execute(query)
        all_agents = list(result.scalars().all())

        # Filter by capability match
        candidates = [
            a for a in all_agents
            if capability in (a.capabilities or [])
        ]

        # Exclude specific agents (for fallback retries)
        if exclude_agent_ids:
            candidates = [a for a in candidates if a.agent_id not in exclude_agent_ids]

        # Filter by budget
        if budget_remaining is not None:
            candidates = [
                a for a in candidates
                if self._get_avg_cost(a) <= budget_remaining
            ]

        if not candidates:
            raise AgentUnavailableError(
                f"No agent available for capability '{capability}'"
                + (f" within budget {budget_remaining}" if budget_remaining else "")
            )

        # Score and rank
        scored = [(a, self._compute_score(a, capability)) for a in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_agent, best_score = scored[0]

        logger.info(
            "agent_selected",
            capability=capability,
            agent_id=str(best_agent.agent_id),
            agent_name=best_agent.name,
            score=round(best_score, 4),
            candidates=len(candidates),
        )

        return best_agent

    async def select_agents_for_dag(
        self,
        dag: DAG,
        budget: float,
    ) -> dict[str, Agent]:
        """Assign agents to all DAG nodes, respecting budget constraints.

        Returns mapping of node_id -> Agent.
        """
        assignments: dict[str, Agent] = {}
        remaining_budget = budget

        for node in dag.topological_sort():
            agent = await self.select_agent(
                capability=node.capability,
                budget_remaining=remaining_budget,
            )
            assignments[node.node_id] = agent
            node.assigned_agent_id = agent.agent_id
            remaining_budget -= self._get_avg_cost(agent)

        return assignments

    def _compute_score(self, agent: Agent, capability: str) -> float:
        """Compute multi-factor selection score for an agent."""
        trust = agent.trust_score

        # Cost efficiency: lower cost = higher score, normalized to [0, 1]
        avg_cost = self._get_avg_cost(agent)
        cost_eff = max(0, 1.0 - min(avg_cost / 0.05, 1.0))  # Normalize to $0.05 max

        # Latency score: lower latency = higher score
        p95_ms = (agent.latency_profile or {}).get("p95_ms", 5000)
        latency_score = max(0, 1.0 - min(p95_ms / 15000, 1.0))  # Normalize to 15s max

        # Capability match: exact match = 1.0
        match_score = 1.0 if capability in (agent.capabilities or []) else 0.0

        # Health: active = 1.0, degraded = 0.5
        health = 1.0 if agent.status == AgentStatus.ACTIVE else 0.5

        score = (
            self.W_TRUST * trust
            + self.W_COST * cost_eff
            + self.W_LATENCY * latency_score
            + self.W_CAPABILITY * match_score
            + self.W_HEALTH * health
        )

        return score

    @staticmethod
    def _get_avg_cost(agent: Agent) -> float:
        return (agent.cost_profile or {}).get("avg_cost_per_call", 0.01)
