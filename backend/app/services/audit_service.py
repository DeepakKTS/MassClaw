from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.models.base import ActorType, AuditEventType
from app.schemas.audit import AuditLogResponse, AuditQueryParams
from app.schemas.common import PaginatedResponse, PaginationParams

logger = get_logger(__name__)

# Redis channel for fire-and-forget audit ingestion
AUDIT_QUEUE_CHANNEL = "massclaw:audit:queue"


class AuditService:
    """Comprehensive audit logging service.

    Provides fire-and-forget logging via Redis pub/sub with a synchronous
    fallback that writes directly to the database if Redis is unavailable.
    Supports full-text search on summaries using PostgreSQL pg_trgm.
    """

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    async def log(
        self,
        event_type: AuditEventType,
        actor_type: ActorType,
        actor_id: str,
        *,
        workflow_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        decision_reason: str | None = None,
        metadata: dict | None = None,
    ) -> AuditLog:
        """Create an audit record.

        Primary path: fire-and-forget via Redis pub/sub so the caller is
        never blocked by a slow database write.  If Redis is unavailable the
        record is written synchronously to the database as a fallback.
        """
        log_id = uuid.uuid4()
        now = datetime.now(UTC)
        meta = metadata or {}

        payload = {
            "log_id": str(log_id),
            "workflow_id": str(workflow_id) if workflow_id else None,
            "task_id": str(task_id) if task_id else None,
            "event_type": event_type.value,
            "actor_type": actor_type.value,
            "actor_id": actor_id,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "decision_reason": decision_reason,
            "metadata": meta,
            "created_at": now.isoformat(),
        }

        try:
            await self.redis.publish(AUDIT_QUEUE_CHANNEL, json.dumps(payload))
            logger.debug(
                "audit_queued",
                log_id=str(log_id),
                event_type=event_type.value,
            )
        except Exception:
            # Redis unavailable — fall through to synchronous DB write
            logger.warning(
                "audit_redis_unavailable_fallback_to_db",
                log_id=str(log_id),
                event_type=event_type.value,
            )
            return await self._write_to_db(
                log_id=log_id,
                workflow_id=workflow_id,
                task_id=task_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                input_summary=input_summary,
                output_summary=output_summary,
                decision_reason=decision_reason,
                metadata=meta,
                created_at=now,
            )

        # Return a transient object so callers can inspect the log_id
        # immediately even though the DB write may be deferred.
        entry = AuditLog(
            log_id=log_id,
            workflow_id=workflow_id,
            task_id=task_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            input_summary=input_summary,
            output_summary=output_summary,
            decision_reason=decision_reason,
            metadata_=meta,
            created_at=now,
        )
        return entry

    async def log_sync(
        self,
        event_type: AuditEventType,
        actor_type: ActorType,
        actor_id: str,
        *,
        workflow_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        decision_reason: str | None = None,
        metadata: dict | None = None,
    ) -> AuditLog:
        """Direct database write — use when Redis queue is not available or
        when the caller needs a guaranteed-persisted record."""
        log_id = uuid.uuid4()
        now = datetime.now(UTC)
        meta = metadata or {}

        return await self._write_to_db(
            log_id=log_id,
            workflow_id=workflow_id,
            task_id=task_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            input_summary=input_summary,
            output_summary=output_summary,
            decision_reason=decision_reason,
            metadata=meta,
            created_at=now,
        )

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    async def query(
        self,
        params: AuditQueryParams,
        pagination: PaginationParams,
    ) -> PaginatedResponse[AuditLogResponse]:
        """Full query with filtering, full-text search, and pagination.

        When ``params.search`` is provided the query uses pg_trgm
        ``similarity()`` against *input_summary* and *output_summary* and
        orders results by best match.
        """
        conditions = self._build_conditions(params)

        # Base query
        query = select(AuditLog)
        count_query = select(func.count()).select_from(AuditLog)

        if conditions:
            combined = and_(*conditions)
            query = query.where(combined)
            count_query = count_query.where(combined)

        # Full-text search via pg_trgm similarity
        if params.search:
            search_term = params.search
            similarity_expr = func.greatest(
                func.coalesce(func.similarity(AuditLog.input_summary, search_term), 0),
                func.coalesce(func.similarity(AuditLog.output_summary, search_term), 0),
            )
            # Only include rows with a minimum similarity threshold
            similarity_filter = similarity_expr > 0.1
            query = query.where(similarity_filter)
            count_query = count_query.where(similarity_filter)
            # Order by best match
            query = query.order_by(similarity_expr.desc(), AuditLog.created_at.desc())
        else:
            query = query.order_by(AuditLog.created_at.desc())

        # Total count
        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        # Pagination
        query = query.offset(pagination.offset).limit(pagination.page_size)

        result = await self.session.execute(query)
        entries = list(result.scalars().all())

        return PaginatedResponse(
            items=[AuditLogResponse.model_validate(e) for e in entries],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    async def get_workflow_audit(
        self,
        workflow_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> PaginatedResponse[AuditLogResponse]:
        """Paginated audit trail for a specific workflow."""
        params = AuditQueryParams(workflow_id=workflow_id)
        return await self.query(params, pagination)

    async def get_agent_audit(
        self,
        agent_id: str,
        pagination: PaginationParams,
    ) -> PaginatedResponse[AuditLogResponse]:
        """Paginated audit trail for a specific agent (actor_id)."""
        params = AuditQueryParams(agent_id=agent_id)
        return await self.query(params, pagination)

    async def count_events(
        self,
        workflow_id: uuid.UUID | None = None,
        since: datetime | None = None,
    ) -> dict[str, int]:
        """Count events grouped by event_type.

        Returns a dict mapping event-type value strings to their counts.
        """
        query = select(
            AuditLog.event_type,
            func.count().label("cnt"),
        ).group_by(AuditLog.event_type)

        conditions: list = []
        if workflow_id is not None:
            conditions.append(AuditLog.workflow_id == workflow_id)
        if since is not None:
            conditions.append(AuditLog.created_at >= since)

        if conditions:
            query = query.where(and_(*conditions))

        result = await self.session.execute(query)
        rows = result.all()

        return {row.event_type.value: row.cnt for row in rows}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_conditions(self, params: AuditQueryParams) -> list:
        """Translate AuditQueryParams into SQLAlchemy filter conditions."""
        conditions: list = []

        if params.workflow_id is not None:
            conditions.append(AuditLog.workflow_id == params.workflow_id)

        if params.agent_id is not None:
            conditions.append(AuditLog.actor_id == params.agent_id)

        if params.event_type is not None:
            conditions.append(AuditLog.event_type == params.event_type)

        if params.actor_type is not None:
            conditions.append(AuditLog.actor_type == params.actor_type)

        if params.since is not None:
            conditions.append(AuditLog.created_at >= params.since)

        if params.until is not None:
            conditions.append(AuditLog.created_at <= params.until)

        return conditions

    async def _write_to_db(
        self,
        *,
        log_id: uuid.UUID,
        workflow_id: uuid.UUID | None,
        task_id: uuid.UUID | None,
        event_type: AuditEventType,
        actor_type: ActorType,
        actor_id: str,
        input_summary: str | None,
        output_summary: str | None,
        decision_reason: str | None,
        metadata: dict,
        created_at: datetime,
    ) -> AuditLog:
        """Persist an audit record directly to the database."""
        entry = AuditLog(
            log_id=log_id,
            workflow_id=workflow_id,
            task_id=task_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            input_summary=input_summary,
            output_summary=output_summary,
            decision_reason=decision_reason,
            metadata_=metadata,
            created_at=created_at,
        )
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)

        logger.info(
            "audit_persisted",
            log_id=str(log_id),
            event_type=event_type.value,
            actor_id=actor_id,
        )

        return entry
