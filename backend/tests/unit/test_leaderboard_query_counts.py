"""Leaderboards must not scale their query count with the page size.

Both leaderboards used to issue one aggregate query per agent on top of the
ranking query, so a default page cost ~21 round trips. These tests pin the
count so the N+1 cannot come back, and pin the numbers themselves so the
collapse into a single GROUP BY did not change any answer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.base import AgentStatus
from app.models.score import AgentScore
from app.models.trust import TrustEvent
from app.services.evolution_service import EvolutionService
from app.services.trust_service import TrustService
from tests.query_counter import count_queries

pytestmark = pytest.mark.asyncio

AGENT_COUNT = 6


async def _agent(session: AsyncSession, *, trust_score: float, safety_level: int = 5) -> Agent:
    agent = Agent(
        name=f"lb-agent-{uuid.uuid4().hex[:8]}",
        description="Agent for leaderboard query-count tests",
        capabilities=["research"],
        endpoint="internal://leaderboard-test",
        status=AgentStatus.ACTIVE,
        trust_score=trust_score,
        safety_level=safety_level,
    )
    session.add(agent)
    await session.flush()
    return agent


async def _trust_event(session: AsyncSession, agent: Agent, composite: float) -> None:
    session.add(
        TrustEvent(
            agent_id=agent.agent_id,
            quality_score=composite,
            latency_score=composite,
            cost_score=composite,
            consistency_score=composite,
            reliability_score=composite,
            composite_score=composite,
            old_trust=0.5,
            new_trust=0.5,
        )
    )
    await session.flush()


async def _score(
    session: AsyncSession,
    agent: Agent,
    composite: float,
    *,
    created_at: datetime | None = None,
) -> None:
    score = AgentScore(
        agent_id=agent.agent_id,
        quality=composite,
        speed=composite,
        cost_efficiency=composite,
        consistency=composite,
        reliability=composite,
        composite=composite,
    )
    if created_at is not None:
        score.created_at = created_at
    session.add(score)
    await session.flush()


class TestTrustLeaderboard:
    async def test_query_count_is_flat_in_the_number_of_agents(self, db_session: AsyncSession, redis_client) -> None:
        agents = [await _agent(db_session, trust_score=0.9 - (i * 0.05)) for i in range(AGENT_COUNT)]
        for agent in agents:
            await _trust_event(db_session, agent, 0.7)
            await _trust_event(db_session, agent, 0.8)

        service = TrustService(db_session, redis_client)
        with count_queries(db_session) as counter:
            board = await service.get_leaderboard(limit=AGENT_COUNT)

        assert len(board) == AGENT_COUNT
        # One SELECT to rank the agents, one aggregate for all of their events.
        # This was AGENT_COUNT + 1 before the collapse.
        assert len(counter.selects) == 2, "\n".join(counter.selects)

    async def test_totals_and_trend_survive_the_collapse(self, db_session: AsyncSession, redis_client) -> None:
        agent = await _agent(db_session, trust_score=0.80)
        await _trust_event(db_session, agent, 0.60)
        await _trust_event(db_session, agent, 0.70)

        service = TrustService(db_session, redis_client)
        board = await service.get_leaderboard(limit=10)
        row = next(r for r in board if r.agent_id == agent.agent_id)

        assert row.total_interactions == 2
        # trend = trust_score - avg(composite) = 0.80 - 0.65
        assert row.trend == pytest.approx(0.15, abs=1e-4)
        assert row.rank >= 1

    async def test_agent_with_no_events_reports_zero_interactions_and_no_trend(
        self, db_session: AsyncSession, redis_client
    ) -> None:
        agent = await _agent(db_session, trust_score=0.77)

        service = TrustService(db_session, redis_client)
        board = await service.get_leaderboard(limit=10)
        row = next(r for r in board if r.agent_id == agent.agent_id)

        # The old per-agent COUNT/AVG returned (0, NULL) for such an agent and
        # the trend fell back to zero. A GROUP BY simply omits the row, so this
        # pins that the fallback is still applied.
        assert row.total_interactions == 0
        assert row.trend == pytest.approx(0.0, abs=1e-9)

    async def test_ranks_are_dense_and_ordered_by_trust(self, db_session: AsyncSession, redis_client) -> None:
        for i in range(4):
            await _agent(db_session, trust_score=0.9 - (i * 0.1))

        service = TrustService(db_session, redis_client)
        board = await service.get_leaderboard(limit=4)

        assert [r.rank for r in board] == [1, 2, 3, 4]
        scores = [r.trust_score for r in board]
        assert scores == sorted(scores, reverse=True)


class TestEvolutionLeaderboard:
    async def test_query_count_is_flat_in_the_number_of_agents(self, db_session: AsyncSession, redis_client) -> None:
        agents = [await _agent(db_session, trust_score=0.5) for _ in range(AGENT_COUNT)]
        for i, agent in enumerate(agents):
            await _score(db_session, agent, 0.9 - (i * 0.05))

        service = EvolutionService(db_session, redis_client)
        with count_queries(db_session) as counter:
            rankings = await service.get_rankings(limit=AGENT_COUNT)

        assert len(rankings) == AGENT_COUNT
        # One SELECT to rank, one for every agent's previous-period average.
        assert len(counter.selects) == 2, "\n".join(counter.selects)

    async def test_all_rows_use_the_same_previous_period_cutoff(self, db_session: AsyncSession, redis_client) -> None:
        """The cutoff used to be recomputed inside the loop, so each row was
        compared against a slightly different seven-day boundary."""
        old = datetime.now(UTC) - timedelta(days=30)
        agents = [await _agent(db_session, trust_score=0.5) for _ in range(3)]
        for agent in agents:
            await _score(db_session, agent, 0.40, created_at=old)
            await _score(db_session, agent, 0.80)

        service = EvolutionService(db_session, redis_client)
        rankings = await service.get_rankings(limit=3)

        # Every agent has the same history, so every delta must be identical.
        deltas = {r.delta_from_previous for r in rankings}
        assert len(deltas) == 1, deltas

    async def test_delta_is_current_minus_previous_period_average(self, db_session: AsyncSession, redis_client) -> None:
        agent = await _agent(db_session, trust_score=0.5)
        await _score(db_session, agent, 0.40, created_at=datetime.now(UTC) - timedelta(days=30))
        await _score(db_session, agent, 0.90)

        service = EvolutionService(db_session, redis_client)
        row = next(r for r in await service.get_rankings(limit=10) if r.agent_id == agent.agent_id)

        # current avg over all scores = 0.65; previous period (>7d old) = 0.40.
        assert row.composite_score == pytest.approx(0.65, abs=1e-4)
        assert row.delta_from_previous == pytest.approx(0.25, abs=1e-4)

    async def test_agent_with_no_previous_period_reports_zero_delta(
        self, db_session: AsyncSession, redis_client
    ) -> None:
        agent = await _agent(db_session, trust_score=0.5)
        await _score(db_session, agent, 0.75)

        service = EvolutionService(db_session, redis_client)
        row = next(r for r in await service.get_rankings(limit=10) if r.agent_id == agent.agent_id)

        # No rows older than the cutoff: prev falls back to the current avg,
        # so the delta is zero rather than the full current value.
        assert row.delta_from_previous == pytest.approx(0.0, abs=1e-9)


class TestPromotionPass:
    """The promotion sweep issued one UPDATE per agent it moved."""

    async def test_updates_are_batched_across_agents(self, db_session: AsyncSession, redis_client) -> None:
        # Ten agents on identical scores: the promote and demote thresholds
        # collapse onto the same value, so every agent qualifies for promotion
        # and the sweep does the most work it can.
        agents = [await _agent(db_session, trust_score=0.5, safety_level=5) for _ in range(10)]
        for agent in agents:
            for _ in range(3):
                await _score(db_session, agent, 0.6)

        service = EvolutionService(db_session, redis_client)
        with (
            patch(
                "app.services.evolution_service.EventBus.publish_dict",
                new_callable=AsyncMock,
                return_value=0,
            ),
            count_queries(db_session) as counter,
        ):
            result = await service.promote_demote_agents()

        assert len(result["promoted"]) == 10
        updates = [s for s in counter.statements if s.lstrip().lower().startswith("update")]
        # At most one UPDATE per direction, rather than one per agent.
        assert len(updates) <= 2, "\n".join(updates)

    async def test_batched_update_still_moves_each_agent_one_level(
        self, db_session: AsyncSession, redis_client
    ) -> None:
        agents = [await _agent(db_session, trust_score=0.5, safety_level=5) for _ in range(10)]
        for agent in agents:
            for _ in range(3):
                await _score(db_session, agent, 0.6)

        service = EvolutionService(db_session, redis_client)
        with patch(
            "app.services.evolution_service.EventBus.publish_dict",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await service.promote_demote_agents()

        for agent in agents:
            await db_session.refresh(agent)
            assert agent.safety_level == 6

    async def test_agents_at_the_ceiling_are_left_alone(self, db_session: AsyncSession, redis_client) -> None:
        """A bulk `safety_level + 1` must not push anyone past 10.

        The scores have to be spread out for this to isolate the ceiling. On
        identical scores the promote threshold (90th percentile) and the demote
        threshold (10th) land on the same value, so an agent blocked from
        promotion by the ceiling falls straight through to the demote branch —
        which is pre-existing behaviour, not what this test is about.
        """
        capped = [await _agent(db_session, trust_score=0.5, safety_level=10) for _ in range(2)]
        for agent in capped:
            for _ in range(3):
                await _score(db_session, agent, 0.90)

        others = [await _agent(db_session, trust_score=0.5, safety_level=5) for _ in range(8)]
        for i, agent in enumerate(others):
            for _ in range(3):
                await _score(db_session, agent, 0.70 - (i * 0.02))

        service = EvolutionService(db_session, redis_client)
        with patch(
            "app.services.evolution_service.EventBus.publish_dict",
            new_callable=AsyncMock,
            return_value=0,
        ):
            result = await service.promote_demote_agents()

        # The two top scorers qualify on score but are blocked by the ceiling.
        assert result["promoted"] == []
        for agent in capped:
            await db_session.refresh(agent)
            assert agent.safety_level == 10
