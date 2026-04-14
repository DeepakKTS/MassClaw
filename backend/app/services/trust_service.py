from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from statistics import mean, stdev

import redis.asyncio as aioredis
from sqlalchemy import and_, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import NotFoundError
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.models.trust import TrustEvent
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.trust import (
    TrustBreakdown,
    TrustEventResponse,
    TrustScoreInput,
    TrustSummary,
)

logger = get_logger(__name__)


class TrustService:
    """Bayesian-weighted exponential moving average trust scoring with temporal decay.

    Trust formula:
        new_trust = (1 - alpha) * decayed_old_trust + alpha * composite_score

    Where:
        alpha = learning_rate / (1 + ln(1 + total_interactions))
        decayed_old_trust = old_trust * decay_factor^(hours_since_last / decay_interval)
        composite = sum(w_i * score_i) for each dimension
    """

    # Absolute bounds — an agent is never fully trusted or fully distrusted
    TRUST_FLOOR = 0.01
    TRUST_CEILING = 0.99

    # Sliding window size for consistency calculation
    CONSISTENCY_WINDOW = 20

    # Bayesian smoothing pseudo-counts for reliability
    RELIABILITY_ALPHA = 2  # prior successes
    RELIABILITY_BETA = 2  # prior failures

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis
        self.settings = get_settings()

    async def record_trust_event(
        self,
        agent_id: uuid.UUID,
        scores: TrustScoreInput,
        workflow_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> TrustEvent:
        """Record a trust event and update the agent's trust score.

        This is the core trust computation:
        1. Compute weighted composite score from input dimensions
        2. Apply Bayesian-weighted EMA with temporal decay
        3. Clamp to [TRUST_FLOOR, TRUST_CEILING]
        4. Persist event and update agent
        """
        # Fetch agent with row-level lock to prevent concurrent trust score
        # overwrites (two concurrent calls for the same agent would otherwise
        # read the same old_trust, compute independently, and the last writer
        # would silently discard the other's update).
        try:
            result = await self.session.execute(select(Agent).where(Agent.agent_id == agent_id).with_for_update())
        except OperationalError:
            logger.error(
                "trust_update_lock_failed",
                agent_id=str(agent_id),
                error="Could not acquire row lock (possible deadlock)",
            )
            raise

        agent = result.scalar_one_or_none()
        if agent is None:
            raise NotFoundError("Agent", str(agent_id))

        old_trust = agent.trust_score
        now = datetime.now(UTC)
        weights = self.settings.trust_score_weights

        # 1. Compute weighted composite from raw dimension scores
        composite = (
            weights["quality"] * scores.quality_score
            + weights["speed"] * scores.latency_score
            + weights["cost"] * scores.cost_score
            + weights["consistency"] * scores.consistency_score
            + weights["reliability"] * scores.reliability_score
        )

        # 2. Count total interactions for this agent
        total_interactions = await self._count_interactions(agent_id)

        # 3. Compute adaptive learning rate (decreases with more data)
        base_lr = self.settings.trust_learning_rate
        alpha = base_lr / (1.0 + math.log(1.0 + total_interactions))

        # 4. Apply temporal decay to old trust
        hours_since_last = await self._hours_since_last_event(agent_id, now)
        decay_rate = self.settings.trust_decay_rate
        decay_interval = self.settings.trust_decay_interval_hours
        decay_factor = math.pow(1.0 - decay_rate, hours_since_last / max(decay_interval, 1))
        decayed_trust = old_trust * decay_factor

        # 5. Bayesian-weighted EMA update
        new_trust = (1.0 - alpha) * decayed_trust + alpha * composite

        # 6. Clamp to bounds
        new_trust = max(self.TRUST_FLOOR, min(self.TRUST_CEILING, new_trust))

        # 7. Persist trust event
        event = TrustEvent(
            agent_id=agent_id,
            workflow_id=workflow_id,
            task_id=task_id,
            quality_score=scores.quality_score,
            latency_score=scores.latency_score,
            cost_score=scores.cost_score,
            consistency_score=scores.consistency_score,
            reliability_score=scores.reliability_score,
            composite_score=composite,
            old_trust=old_trust,
            new_trust=new_trust,
        )
        self.session.add(event)

        # 8. Update agent trust score
        agent.trust_score = new_trust
        await self.session.flush()
        await self.session.refresh(event)

        logger.info(
            "trust_updated",
            agent_id=str(agent_id),
            old_trust=round(old_trust, 4),
            new_trust=round(new_trust, 4),
            composite=round(composite, 4),
            alpha=round(alpha, 4),
            decay_factor=round(decay_factor, 4),
            total_interactions=total_interactions + 1,
        )

        # 9. Publish event
        await EventBus.publish_dict(
            ["trust", str(agent_id), "updated"],
            "trust.updated",
            {
                "agent_id": str(agent_id),
                "old_trust": round(old_trust, 4),
                "new_trust": round(new_trust, 4),
                "composite": round(composite, 4),
            },
        )

        return event

    async def compute_consistency_score(self, agent_id: uuid.UUID) -> float:
        """Compute consistency as 1 - coefficient_of_variation over recent quality scores.

        A consistent agent has low variance in quality output.
        Returns 0-1 where 1 = perfectly consistent.
        """
        result = await self.session.execute(
            select(TrustEvent.quality_score)
            .where(TrustEvent.agent_id == agent_id)
            .order_by(TrustEvent.created_at.desc())
            .limit(self.CONSISTENCY_WINDOW)
        )
        scores = [row[0] for row in result.all()]

        if len(scores) < 2:
            return 0.5  # Not enough data, return neutral

        avg = mean(scores)
        if avg == 0:
            return 0.5

        sd = stdev(scores)
        cv = sd / avg  # coefficient of variation
        # Clamp CV to [0, 2] and invert to [0, 1]
        consistency = 1.0 - min(cv, 2.0) / 2.0
        return max(0.0, min(1.0, consistency))

    async def compute_reliability_score(self, agent_id: uuid.UUID) -> float:
        """Compute reliability using Bayesian smoothing.

        reliability = (successes + alpha) / (total + alpha + beta)

        Where alpha/beta are pseudo-counts that prevent extreme values
        when data is sparse.
        """
        # Count successful tasks (quality > 0.5) and total tasks
        result = await self.session.execute(
            select(
                func.count().label("total"),
                func.count().filter(TrustEvent.quality_score > 0.5).label("successes"),
            ).where(TrustEvent.agent_id == agent_id)
        )
        row = result.one()
        total = row.total
        successes = row.successes

        # Bayesian smoothing
        reliability = (successes + self.RELIABILITY_ALPHA) / (total + self.RELIABILITY_ALPHA + self.RELIABILITY_BETA)
        return max(0.0, min(1.0, reliability))

    async def get_trust_breakdown(self, agent_id: uuid.UUID) -> TrustBreakdown:
        """Get detailed trust breakdown for an agent."""
        # Verify agent exists
        agent_result = await self.session.execute(select(Agent).where(Agent.agent_id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise NotFoundError("Agent", str(agent_id))

        # Compute average dimension scores
        result = await self.session.execute(
            select(
                func.avg(TrustEvent.quality_score).label("quality_avg"),
                func.avg(TrustEvent.latency_score).label("speed_avg"),
                func.avg(TrustEvent.cost_score).label("cost_avg"),
                func.avg(TrustEvent.consistency_score).label("consistency_avg"),
                func.avg(TrustEvent.reliability_score).label("reliability_avg"),
                func.count().label("total_interactions"),
                func.max(TrustEvent.created_at).label("last_updated"),
            ).where(TrustEvent.agent_id == agent_id)
        )
        row = result.one()

        return TrustBreakdown(
            agent_id=agent_id,
            current_trust=agent.trust_score,
            quality_avg=float(row.quality_avg or 0),
            speed_avg=float(row.speed_avg or 0),
            cost_avg=float(row.cost_avg or 0),
            consistency_avg=float(row.consistency_avg or 0),
            reliability_avg=float(row.reliability_avg or 0),
            total_interactions=row.total_interactions,
            last_updated=row.last_updated,
        )

    async def get_trust_history(
        self,
        agent_id: uuid.UUID,
        pagination: PaginationParams,
        since: datetime | None = None,
    ) -> PaginatedResponse[TrustEventResponse]:
        """Get paginated trust event history for an agent."""
        # Verify agent exists
        agent_result = await self.session.execute(select(Agent.agent_id).where(Agent.agent_id == agent_id))
        if agent_result.scalar_one_or_none() is None:
            raise NotFoundError("Agent", str(agent_id))

        conditions = [TrustEvent.agent_id == agent_id]
        if since:
            conditions.append(TrustEvent.created_at >= since)

        # Count
        count_result = await self.session.execute(select(func.count()).select_from(TrustEvent).where(and_(*conditions)))
        total = count_result.scalar_one()

        # Fetch
        result = await self.session.execute(
            select(TrustEvent)
            .where(and_(*conditions))
            .order_by(TrustEvent.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        events = list(result.scalars().all())

        return PaginatedResponse(
            items=[TrustEventResponse.model_validate(e) for e in events],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def get_leaderboard(
        self,
        limit: int = 20,
        capability_filter: list[str] | None = None,
    ) -> list[TrustSummary]:
        """Get ranked agents by trust score."""
        query = select(
            Agent.agent_id,
            Agent.name.label("agent_name"),
            Agent.trust_score,
        ).where(Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.DEGRADED]))

        if capability_filter:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB

            query = query.where(Agent.capabilities.op("@>")(cast(capability_filter, JSONB)))

        query = query.order_by(Agent.trust_score.desc()).limit(limit)

        result = await self.session.execute(query)
        rows = result.all()

        leaderboard = []
        for rank, row in enumerate(rows, start=1):
            # Compute interaction count and trend per agent
            event_stats = await self.session.execute(
                select(
                    func.count().label("total"),
                    func.avg(TrustEvent.composite_score).label("recent_avg"),
                ).where(TrustEvent.agent_id == row.agent_id)
            )
            stats = event_stats.one()

            # Trend: difference between current trust and average composite
            trend = row.trust_score - float(stats.recent_avg or row.trust_score)

            leaderboard.append(
                TrustSummary(
                    agent_id=row.agent_id,
                    agent_name=row.agent_name,
                    trust_score=row.trust_score,
                    total_interactions=stats.total,
                    trend=round(trend, 4),
                    rank=rank,
                )
            )

        return leaderboard

    async def decay_all_scores(self) -> int:
        """Background task: decay trust scores for agents with no recent activity.

        Returns the number of agents whose trust was decayed.
        """
        now = datetime.now(UTC)
        decay_rate = self.settings.trust_decay_rate
        decay_interval_hours = self.settings.trust_decay_interval_hours
        decayed_count = 0

        # Find active agents with skip_locked so batch decay doesn't block
        # individual trust updates that are holding a row lock.
        result = await self.session.execute(
            select(Agent)
            .where(Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.DEGRADED]))
            .with_for_update(skip_locked=True)
        )
        agents = result.scalars().all()

        for agent in agents:
            hours_since = await self._hours_since_last_event(agent.agent_id, now)

            if hours_since < decay_interval_hours:
                continue  # Recent activity, skip

            decay_factor = math.pow(1.0 - decay_rate, hours_since / max(decay_interval_hours, 1))
            old_trust = agent.trust_score
            new_trust = max(self.TRUST_FLOOR, old_trust * decay_factor)

            if abs(new_trust - old_trust) > 0.0001:
                agent.trust_score = new_trust
                decayed_count += 1

                logger.debug(
                    "trust_decayed",
                    agent_id=str(agent.agent_id),
                    old=round(old_trust, 4),
                    new=round(new_trust, 4),
                    hours_inactive=round(hours_since, 1),
                )

        if decayed_count > 0:
            await self.session.flush()
            logger.info("trust_decay_batch", decayed_agents=decayed_count)

        return decayed_count

    # --- Private helpers ---

    async def _count_interactions(self, agent_id: uuid.UUID) -> int:
        """Count total trust events for an agent."""
        result = await self.session.execute(
            select(func.count()).select_from(TrustEvent).where(TrustEvent.agent_id == agent_id)
        )
        return result.scalar_one()

    async def _hours_since_last_event(self, agent_id: uuid.UUID, now: datetime) -> float:
        """Hours since the most recent trust event for this agent."""
        result = await self.session.execute(
            select(func.max(TrustEvent.created_at)).where(TrustEvent.agent_id == agent_id)
        )
        last_event_time = result.scalar_one_or_none()
        if last_event_time is None:
            return 0.0

        # Ensure both are timezone-aware
        if last_event_time.tzinfo is None:
            last_event_time = last_event_time.replace(tzinfo=UTC)

        delta = now - last_event_time
        return delta.total_seconds() / 3600.0
