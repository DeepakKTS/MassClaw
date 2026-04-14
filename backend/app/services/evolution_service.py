from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.models.score import AgentScore
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.score import (
    AgentEvolution,
    AgentRanking,
    AgentScoreResponse,
    DimensionScores,
    ScoreInput,
)

logger = get_logger(__name__)


class EvolutionService:
    """Multi-dimensional agent scoring, historical trends, and auto promotion/demotion.

    Scoring: composite = weighted combination of quality, speed, cost_efficiency,
    consistency, reliability. Weights from config.

    Promotion/demotion: agents in top 10% get safety_level increased,
    bottom 10% get decreased. Evaluated periodically via background worker.
    """

    # Percentile thresholds for promotion/demotion
    PROMOTE_PERCENTILE = 90
    DEMOTE_PERCENTILE = 10

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis
        self.settings = get_settings()

    async def record_score(
        self,
        agent_id: uuid.UUID,
        scores: ScoreInput,
        workflow_id: uuid.UUID | None = None,
    ) -> AgentScore:
        """Record a score snapshot for an agent after a workflow task.

        Computes the weighted composite and persists the record.
        """
        # Verify agent exists
        agent_result = await self.session.execute(select(Agent).where(Agent.agent_id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise NotFoundError("Agent", str(agent_id))

        weights = self.settings.trust_score_weights
        composite = (
            weights["quality"] * scores.quality
            + weights["speed"] * scores.speed
            + weights["cost"] * scores.cost_efficiency
            + weights["consistency"] * scores.consistency
            + weights["reliability"] * scores.reliability
        )

        record = AgentScore(
            agent_id=agent_id,
            workflow_id=workflow_id,
            quality=scores.quality,
            speed=scores.speed,
            cost_efficiency=scores.cost_efficiency,
            consistency=scores.consistency,
            reliability=scores.reliability,
            composite=round(composite, 4),
        )
        self.session.add(record)
        await self.session.flush()
        await self.session.refresh(record)

        # Update running composite in Redis for fast leaderboard queries
        await self.redis.zadd(
            "evolution:rankings",
            {str(agent_id): composite},
        )

        logger.info(
            "score_recorded",
            agent_id=str(agent_id),
            composite=round(composite, 4),
            quality=scores.quality,
            speed=scores.speed,
        )

        await EventBus.publish_dict(
            ["evolution", str(agent_id), "scored"],
            "score.recorded",
            {"agent_id": str(agent_id), "composite": round(composite, 4)},
        )

        return record

    async def get_agent_evolution(self, agent_id: uuid.UUID) -> AgentEvolution:
        """Get comprehensive evolution data for an agent: current scores,
        percentile ranking, trend analysis, and dimension breakdown."""
        # Verify agent
        agent_result = await self.session.execute(select(Agent).where(Agent.agent_id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise NotFoundError("Agent", str(agent_id))

        # Current dimension averages (last 30 days)
        now = datetime.now(UTC)
        thirty_days_ago = now - timedelta(days=30)

        avg_result = await self.session.execute(
            select(
                func.avg(AgentScore.quality).label("quality"),
                func.avg(AgentScore.speed).label("speed"),
                func.avg(AgentScore.cost_efficiency).label("cost_efficiency"),
                func.avg(AgentScore.consistency).label("consistency"),
                func.avg(AgentScore.reliability).label("reliability"),
                func.avg(AgentScore.composite).label("composite"),
                func.count().label("total"),
            ).where(
                and_(
                    AgentScore.agent_id == agent_id,
                    AgentScore.created_at >= thirty_days_ago,
                )
            )
        )
        avg = avg_result.one()

        # Trend: compare last 7 days vs previous 7 days
        seven_days_ago = now - timedelta(days=7)
        fourteen_days_ago = now - timedelta(days=14)

        recent_result = await self.session.execute(
            select(func.avg(AgentScore.composite)).where(
                and_(
                    AgentScore.agent_id == agent_id,
                    AgentScore.created_at >= seven_days_ago,
                )
            )
        )
        recent_avg = recent_result.scalar_one() or 0.0

        previous_result = await self.session.execute(
            select(func.avg(AgentScore.composite)).where(
                and_(
                    AgentScore.agent_id == agent_id,
                    AgentScore.created_at >= fourteen_days_ago,
                    AgentScore.created_at < seven_days_ago,
                )
            )
        )
        previous_avg = previous_result.scalar_one() or float(recent_avg)
        trend_7d = float(recent_avg) - float(previous_avg)

        # Percentile ranking among all agents
        percentile = await self._compute_percentile(agent_id)

        return AgentEvolution(
            agent_id=agent_id,
            agent_name=agent.name,
            current_composite=round(float(avg.composite or 0), 4),
            percentile=round(percentile, 1),
            trend_7d=round(trend_7d, 4),
            dimensions=DimensionScores(
                quality=round(float(avg.quality or 0), 4),
                speed=round(float(avg.speed or 0), 4),
                cost_efficiency=round(float(avg.cost_efficiency or 0), 4),
                consistency=round(float(avg.consistency or 0), 4),
                reliability=round(float(avg.reliability or 0), 4),
            ),
            total_scored_tasks=avg.total,
        )

    async def get_score_history(
        self,
        agent_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> PaginatedResponse[AgentScoreResponse]:
        """Get paginated score history for an agent."""
        # Verify agent
        agent_result = await self.session.execute(select(Agent.agent_id).where(Agent.agent_id == agent_id))
        if agent_result.scalar_one_or_none() is None:
            raise NotFoundError("Agent", str(agent_id))

        count_result = await self.session.execute(
            select(func.count()).select_from(AgentScore).where(AgentScore.agent_id == agent_id)
        )
        total = count_result.scalar_one()

        result = await self.session.execute(
            select(AgentScore)
            .where(AgentScore.agent_id == agent_id)
            .order_by(AgentScore.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        scores = list(result.scalars().all())

        return PaginatedResponse(
            items=[AgentScoreResponse.model_validate(s) for s in scores],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def get_rankings(
        self,
        limit: int = 20,
        capability: str | None = None,
        sort_by: str = "composite",
    ) -> list[AgentRanking]:
        """Get ranked agents by composite score or specific dimension."""
        # Query agents with their latest average composite score
        query = (
            select(
                Agent.agent_id,
                Agent.name.label("agent_name"),
                func.avg(AgentScore.composite).label("avg_composite"),
                func.count(AgentScore.score_id).label("total_tasks"),
            )
            .join(AgentScore, Agent.agent_id == AgentScore.agent_id, isouter=True)
            .where(Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.DEGRADED]))
            .group_by(Agent.agent_id, Agent.name)
        )

        if capability:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB

            query = query.where(Agent.capabilities.op("@>")(cast([capability], JSONB)))

        query = query.order_by(func.coalesce(func.avg(AgentScore.composite), 0).desc())
        query = query.limit(limit)

        result = await self.session.execute(query)
        rows = result.all()

        # Compute delta from previous period (7 days ago)
        rankings: list[AgentRanking] = []
        for rank, row in enumerate(rows, start=1):
            seven_days_ago = datetime.now(UTC) - timedelta(days=7)
            prev_result = await self.session.execute(
                select(func.avg(AgentScore.composite)).where(
                    and_(
                        AgentScore.agent_id == row.agent_id,
                        AgentScore.created_at < seven_days_ago,
                    )
                )
            )
            prev_avg = prev_result.scalar_one()
            current_avg = float(row.avg_composite or 0)
            delta = current_avg - float(prev_avg or current_avg)

            rankings.append(
                AgentRanking(
                    rank=rank,
                    agent_id=row.agent_id,
                    agent_name=row.agent_name,
                    composite_score=round(current_avg, 4),
                    delta_from_previous=round(delta, 4),
                    total_tasks=row.total_tasks,
                )
            )

        return rankings

    async def promote_demote_agents(self) -> dict[str, list[str]]:
        """Background task: promote top 10% and demote bottom 10% agents.

        Promotion = increase safety_level (more trusted, fewer policy checks).
        Demotion = decrease safety_level (more scrutiny).

        Returns dict with 'promoted' and 'demoted' agent name lists.
        """
        # Get all active agents with their average composite scores
        result = await self.session.execute(
            select(
                Agent.agent_id,
                Agent.name,
                Agent.safety_level,
                func.avg(AgentScore.composite).label("avg_composite"),
            )
            .join(AgentScore, Agent.agent_id == AgentScore.agent_id)
            .where(Agent.status == AgentStatus.ACTIVE)
            .group_by(Agent.agent_id, Agent.name, Agent.safety_level)
            .having(func.count(AgentScore.score_id) >= 3)  # Minimum 3 scores to qualify
            .order_by(func.avg(AgentScore.composite).desc())
        )
        rows = result.all()

        if len(rows) < 5:
            return {"promoted": [], "demoted": []}

        # Calculate thresholds (composites list is sorted DESC)
        composites = [float(r.avg_composite) for r in rows]
        n = len(composites)
        promote_threshold_idx = max(0, int(n * (1 - self.PROMOTE_PERCENTILE / 100)))
        demote_threshold_idx = min(n - 1, int(n * (1 - self.DEMOTE_PERCENTILE / 100)))

        promote_threshold = composites[promote_threshold_idx]
        demote_threshold = composites[demote_threshold_idx]

        promoted: list[str] = []
        demoted: list[str] = []

        for row in rows:
            avg = float(row.avg_composite)

            if avg >= promote_threshold and row.safety_level < 10:
                await self.session.execute(
                    update(Agent)
                    .where(Agent.agent_id == row.agent_id)
                    .values(safety_level=min(10, row.safety_level + 1))
                )
                promoted.append(row.name)
                logger.info("agent_promoted", agent=row.name, new_level=row.safety_level + 1)
                await EventBus.publish_dict(
                    ["evolution", str(row.agent_id), "promoted"],
                    "agent.promoted",
                    {"agent_id": str(row.agent_id), "name": row.name, "new_level": row.safety_level + 1},
                )

            elif avg <= demote_threshold and row.safety_level > 1:
                await self.session.execute(
                    update(Agent)
                    .where(Agent.agent_id == row.agent_id)
                    .values(safety_level=max(1, row.safety_level - 1))
                )
                demoted.append(row.name)
                logger.info("agent_demoted", agent=row.name, new_level=row.safety_level - 1)
                await EventBus.publish_dict(
                    ["evolution", str(row.agent_id), "demoted"],
                    "agent.demoted",
                    {"agent_id": str(row.agent_id), "name": row.name, "new_level": row.safety_level - 1},
                )

        if promoted or demoted:
            await self.session.flush()
            logger.info(
                "promotion_demotion_complete",
                promoted=len(promoted),
                demoted=len(demoted),
            )

        return {"promoted": promoted, "demoted": demoted}

    async def _compute_percentile(self, agent_id: uuid.UUID) -> float:
        """Compute the percentile ranking of an agent among all scored agents."""
        # Get this agent's average composite
        my_result = await self.session.execute(
            select(func.avg(AgentScore.composite)).where(AgentScore.agent_id == agent_id)
        )
        my_avg = my_result.scalar_one()
        if my_avg is None:
            return 50.0  # Default to median if no scores

        # Count agents with lower average composite
        total_result = await self.session.execute(
            select(func.count(func.distinct(AgentScore.agent_id))).select_from(AgentScore)
        )
        total_agents = total_result.scalar_one()

        if total_agents <= 1:
            return 50.0

        lower_result = await self.session.execute(
            select(func.count()).select_from(
                select(AgentScore.agent_id)
                .group_by(AgentScore.agent_id)
                .having(func.avg(AgentScore.composite) < float(my_avg))
                .subquery()
            )
        )
        lower_count = lower_result.scalar_one()

        return (lower_count / total_agents) * 100
