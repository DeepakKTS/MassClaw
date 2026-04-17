"""Unit tests for agent evolution scoring, leaderboard, and promotion/demotion."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.base import AgentStatus
from app.models.score import AgentScore
from app.schemas.score import ScoreInput
from app.services.evolution_service import EvolutionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default weights from Settings.trust_score_weights:
#   quality=0.35, speed=0.15, cost=0.15, consistency=0.15, reliability=0.20
_WEIGHTS = {
    "quality": 0.35,
    "speed": 0.15,
    "cost": 0.15,
    "consistency": 0.15,
    "reliability": 0.20,
}


def _expected_composite(q: float, s: float, ce: float, co: float, r: float) -> float:
    return round(
        _WEIGHTS["quality"] * q
        + _WEIGHTS["speed"] * s
        + _WEIGHTS["cost"] * ce
        + _WEIGHTS["consistency"] * co
        + _WEIGHTS["reliability"] * r,
        4,
    )


async def _create_agent(
    session: AsyncSession,
    *,
    name: str | None = None,
    trust_score: float = 0.75,
    safety_level: int = 5,
) -> Agent:
    agent = Agent(
        name=name or f"evo-agent-{uuid.uuid4().hex[:8]}",
        description="Agent for evolution tests",
        capabilities=["research", "analysis"],
        endpoint="internal://evo-test",
        trust_score=trust_score,
        status=AgentStatus.ACTIVE,
        safety_level=safety_level,
        cost_profile={"avg_cost_per_call": 0.01},
        latency_profile={"p50_ms": 300, "p95_ms": 1200},
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvolutionService:
    """Tests for EvolutionService: scoring, leaderboard, and promotion/demotion."""

    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return EvolutionService(db_session, redis_client)

    # --- 1. Record a performance score and verify persistence ---

    @pytest.mark.asyncio
    async def test_record_score(self, db_session, redis_client, service):
        """Recording a score should persist the record with the correct composite value."""
        agent = await _create_agent(db_session)

        scores = ScoreInput(quality=0.9, speed=0.8, cost_efficiency=0.7, consistency=0.85, reliability=0.95)

        with patch("app.services.evolution_service.EventBus.publish_dict", new_callable=AsyncMock, return_value=0):
            record = await service.record_score(agent.agent_id, scores)

        # Verify returned record
        assert record.agent_id == agent.agent_id
        assert record.quality == 0.9
        assert record.speed == 0.8
        assert record.cost_efficiency == 0.7
        assert record.consistency == 0.85
        assert record.reliability == 0.95

        expected = _expected_composite(0.9, 0.8, 0.7, 0.85, 0.95)
        assert record.composite == expected

        # Verify persistence in DB
        result = await db_session.execute(select(AgentScore).where(AgentScore.score_id == record.score_id))
        persisted = result.scalar_one()
        assert persisted.composite == expected

    # --- 2. Leaderboard ranking order ---

    @pytest.mark.asyncio
    async def test_leaderboard_ranking(self, db_session, redis_client, service):
        """Agents with higher composite scores should rank above those with lower scores."""
        # Create agents with distinct performance profiles
        agent_high = await _create_agent(db_session, name="evo-high")
        agent_mid = await _create_agent(db_session, name="evo-mid")
        agent_low = await _create_agent(db_session, name="evo-low")

        high_scores = ScoreInput(quality=0.95, speed=0.9, cost_efficiency=0.9, consistency=0.9, reliability=0.95)
        mid_scores = ScoreInput(quality=0.6, speed=0.6, cost_efficiency=0.6, consistency=0.6, reliability=0.6)
        low_scores = ScoreInput(quality=0.2, speed=0.2, cost_efficiency=0.2, consistency=0.2, reliability=0.2)

        with patch("app.services.evolution_service.EventBus.publish_dict", new_callable=AsyncMock, return_value=0):
            await service.record_score(agent_high.agent_id, high_scores)
            await service.record_score(agent_mid.agent_id, mid_scores)
            await service.record_score(agent_low.agent_id, low_scores)

        rankings = await service.get_rankings(limit=10)

        # Extract agent names in ranked order
        names = [r.agent_name for r in rankings]
        # The high-scorer must appear before the mid, and mid before low
        assert names.index("evo-high") < names.index("evo-mid")
        assert names.index("evo-mid") < names.index("evo-low")

    # --- 3. Promotion threshold: top 10% promoted ---

    @pytest.mark.asyncio
    async def test_promotion_threshold(self, db_session, redis_client, service):
        """Agents in the top 10% by average composite should have safety_level increased."""
        agents = []
        # Create 10 agents so top 10% = 1 agent
        for i in range(10):
            a = await _create_agent(db_session, name=f"evo-promo-{i}", safety_level=5)
            agents.append(a)

        # Record 3+ scores each (minimum to qualify).
        # Give agent-0 the highest scores, the rest mediocre.
        with patch("app.services.evolution_service.EventBus.publish_dict", new_callable=AsyncMock, return_value=0):
            for rep in range(3):
                # Top agent — high scores
                await service.record_score(
                    agents[0].agent_id,
                    ScoreInput(quality=0.99, speed=0.99, cost_efficiency=0.99, consistency=0.99, reliability=0.99),
                )
                # Remaining agents — mediocre scores
                for a in agents[1:]:
                    await service.record_score(
                        a.agent_id,
                        ScoreInput(quality=0.5, speed=0.5, cost_efficiency=0.5, consistency=0.5, reliability=0.5),
                    )

            result = await service.promote_demote_agents()

        assert agents[0].name in result["promoted"]
        # Verify DB reflects the increased safety_level
        refreshed = await db_session.get(Agent, agents[0].agent_id)
        assert refreshed.safety_level == 6  # was 5, promoted by 1

    # --- 4. Demotion threshold: bottom 10% demoted ---

    @pytest.mark.asyncio
    async def test_demotion_threshold(self, db_session, redis_client, service):
        """Agents in the bottom 10% by average composite should have safety_level decreased."""
        agents = []
        for i in range(10):
            a = await _create_agent(db_session, name=f"evo-demo-{i}", safety_level=5)
            agents.append(a)

        with patch("app.services.evolution_service.EventBus.publish_dict", new_callable=AsyncMock, return_value=0):
            for rep in range(3):
                # Bottom agent — poor scores
                await service.record_score(
                    agents[-1].agent_id,
                    ScoreInput(quality=0.01, speed=0.01, cost_efficiency=0.01, consistency=0.01, reliability=0.01),
                )
                # Remaining agents — good scores
                for a in agents[:-1]:
                    await service.record_score(
                        a.agent_id,
                        ScoreInput(quality=0.85, speed=0.85, cost_efficiency=0.85, consistency=0.85, reliability=0.85),
                    )

            result = await service.promote_demote_agents()

        assert agents[-1].name in result["demoted"]
        # Verify DB reflects the decreased safety_level
        refreshed = await db_session.get(Agent, agents[-1].agent_id)
        assert refreshed.safety_level == 4  # was 5, demoted by 1
