from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import httpx
import redis.asyncio as aioredis
from sqlalchemy import and_, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import ConflictError, NotFoundError
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.schemas.agent import (
    AgentCreate,
    AgentSearchParams,
    AgentUpdate,
    HealthCheckResponse,
)
from app.schemas.common import PaginatedResponse, PaginationParams, SortParams

logger = get_logger(__name__)


class AgentService:
    """Business logic for agent management."""

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def create_agent(self, data: AgentCreate) -> Agent:
        """Register a new agent."""
        # Check uniqueness
        existing = await self.session.execute(select(Agent).where(Agent.name == data.name))
        if existing.scalar_one_or_none():
            raise ConflictError(f"Agent with name '{data.name}' already exists")

        agent = Agent(
            name=data.name,
            description=data.description,
            capabilities=data.capabilities,
            endpoint=data.endpoint,
            supported_tools=data.supported_tools,
            cost_profile=data.cost_profile,
            latency_profile=data.latency_profile,
            version=data.version,
            safety_level=data.safety_level,
            input_schema=data.input_schema,
            output_schema=data.output_schema,
            health_check_url=data.health_check_url,
            metadata_=data.metadata,
        )
        self.session.add(agent)
        await self.session.flush()

        logger.info("agent_registered", agent_id=str(agent.agent_id), name=agent.name)

        await EventBus.publish_dict(
            ["agent", str(agent.agent_id), "registered"],
            "agent.registered",
            {"agent_id": str(agent.agent_id), "name": agent.name},
        )

        return agent

    async def get_agent(self, agent_id: uuid.UUID) -> Agent:
        """Get an agent by ID."""
        result = await self.session.execute(select(Agent).where(Agent.agent_id == agent_id))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise NotFoundError("Agent", str(agent_id))
        return agent

    async def list_agents(
        self,
        pagination: PaginationParams,
        sort: SortParams,
        filters: AgentSearchParams | None = None,
    ) -> PaginatedResponse[Agent]:
        """List agents with pagination, sorting, and filtering."""
        query = select(Agent)
        count_query = select(func.count()).select_from(Agent)

        # Apply filters
        if filters:
            conditions = self._build_filter_conditions(filters)
            if conditions:
                combined = and_(*conditions)
                query = query.where(combined)
                count_query = count_query.where(combined)

        # Get total count
        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        # Apply sorting
        sort_column = getattr(Agent, sort.sort_by, Agent.created_at)
        if sort.sort_order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

        # Apply pagination
        query = query.offset(pagination.offset).limit(pagination.page_size)

        result = await self.session.execute(query)
        agents = list(result.scalars().all())

        return PaginatedResponse(
            items=agents,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def update_agent(self, agent_id: uuid.UUID, data: AgentUpdate) -> Agent:
        """Update an agent. Uses optimistic locking via updated_at check."""
        agent = await self.get_agent(agent_id)

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return agent

        # Handle metadata field name mapping
        if "metadata" in update_data:
            update_data["metadata_"] = update_data.pop("metadata")

        for field, value in update_data.items():
            setattr(agent, field, value)

        await self.session.flush()
        # Refresh to load server-side defaults (updated_at from onupdate=func.now())
        await self.session.refresh(agent)

        logger.info("agent_updated", agent_id=str(agent_id), fields=list(update_data.keys()))

        await EventBus.publish_dict(
            ["agent", str(agent_id), "updated"],
            "agent.updated",
            {"agent_id": str(agent_id), "fields": list(update_data.keys())},
        )

        return agent

    async def delete_agent(self, agent_id: uuid.UUID) -> None:
        """Soft-delete an agent (set status to inactive)."""
        agent = await self.get_agent(agent_id)
        agent.status = AgentStatus.INACTIVE

        await self.session.flush()

        logger.info("agent_deactivated", agent_id=str(agent_id))

        await EventBus.publish_dict(
            ["agent", str(agent_id), "deactivated"],
            "agent.deactivated",
            {"agent_id": str(agent_id)},
        )

    async def search_agents(
        self,
        filters: AgentSearchParams,
        limit: int = 20,
    ) -> list[Agent]:
        """Discovery query: find agents matching capabilities with multi-factor ranking.

        Agents are ranked by a composite score considering:
        - trust_score (primary)
        - cost efficiency (from cost_profile)
        - capability match coverage
        """
        query = select(Agent).where(Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.DEGRADED]))

        conditions = self._build_filter_conditions(filters)
        if conditions:
            query = query.where(and_(*conditions))

        # Order by trust_score descending (primary ranking factor)
        query = query.order_by(Agent.trust_score.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def health_check(self, agent_id: uuid.UUID) -> HealthCheckResponse:
        """Perform a health check on an agent."""
        agent = await self.get_agent(agent_id)
        now = datetime.now(UTC)

        if not agent.health_check_url:
            return HealthCheckResponse(
                agent_id=agent.agent_id,
                healthy=agent.status == AgentStatus.ACTIVE,
                status=agent.status,
                checked_at=now,
                error="No health check URL configured",
            )

        latency_ms: float | None = None
        error: str | None = None
        healthy = False

        try:
            start = time.perf_counter()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(agent.health_check_url)
            latency_ms = (time.perf_counter() - start) * 1000
            healthy = response.status_code == 200
        except Exception as e:
            error = str(e)

        agent.last_health_check = now

        # Track consecutive failures in Redis
        failure_key = f"agent_health_failures:{agent_id}"
        if healthy:
            await self.redis.delete(failure_key)
            if agent.status == AgentStatus.DEGRADED:
                agent.status = AgentStatus.ACTIVE
                logger.info("agent_recovered", agent_id=str(agent_id))
        else:
            failures = await self.redis.incr(failure_key)
            await self.redis.expire(failure_key, 3600)

            if failures >= 5 and agent.status != AgentStatus.SUSPENDED:
                agent.status = AgentStatus.SUSPENDED
                logger.warning("agent_suspended", agent_id=str(agent_id), failures=failures)
            elif failures >= 3 and agent.status == AgentStatus.ACTIVE:
                agent.status = AgentStatus.DEGRADED
                logger.warning("agent_degraded", agent_id=str(agent_id), failures=failures)

        await self.session.flush()

        return HealthCheckResponse(
            agent_id=agent.agent_id,
            healthy=healthy,
            status=agent.status,
            latency_ms=latency_ms,
            checked_at=now,
            error=error,
        )

    def _build_filter_conditions(self, filters: AgentSearchParams) -> list:
        """Build SQLAlchemy filter conditions from search params."""
        conditions = []

        if filters.capabilities:
            # Use JSONB @> containment operator
            conditions.append(Agent.capabilities.op("@>")(cast(filters.capabilities, JSONB)))

        if filters.status:
            conditions.append(Agent.status == filters.status)

        if filters.min_trust is not None:
            conditions.append(Agent.trust_score >= filters.min_trust)

        if filters.max_cost is not None:
            # Filter by avg_cost_per_call from cost_profile JSONB
            conditions.append(
                cast(
                    Agent.cost_profile["avg_cost_per_call"].as_string(),
                    type_=Agent.trust_score.type,
                )
                <= filters.max_cost
            )

        if filters.name_query:
            # Trigram similarity search
            conditions.append(func.similarity(Agent.name, filters.name_query) > 0.3)

        return conditions
