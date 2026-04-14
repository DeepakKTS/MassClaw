from __future__ import annotations

import uuid
from decimal import Decimal

import redis.asyncio as aioredis
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventBus
from app.core.logging import get_logger
from app.exceptions import BudgetExhaustedError, NotFoundError, ValidationError
from app.models.base import WalletActionType
from app.models.wallet import WalletEvent
from app.models.workflow import Workflow
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.wallet import (
    WalletBalanceResponse,
    WalletEventResponse,
)

logger = get_logger(__name__)


class WalletService:
    """Event-sourced wallet with atomic budget reservations.

    Design principles:
    - Balance is derived from immutable WalletEvent records: SUM(credit_delta)
    - Redis caches the balance for fast reads; events are the source of truth
    - Reservations use SELECT FOR UPDATE on the workflow row to prevent race conditions
    - Every state change produces an auditable event
    """

    BALANCE_CACHE_PREFIX = "wallet_balance:"
    BALANCE_CACHE_TTL = 30  # seconds

    def __init__(self, session: AsyncSession, redis: aioredis.Redis) -> None:
        self.session = session
        self.redis = redis

    async def get_balance(self, workflow_id: uuid.UUID) -> WalletBalanceResponse:
        """Get current wallet balance for a workflow.

        Checks Redis cache first, falls back to computing from events.
        """
        workflow = await self._get_workflow(workflow_id)

        # Try Redis cache
        cache_key = f"{self.BALANCE_CACHE_PREFIX}{workflow_id}"
        cached = await self.redis.get(cache_key)

        if cached is not None:
            budget_used = float(cached)
        else:
            budget_used = await self._compute_balance_from_events(workflow_id)
            await self.redis.set(cache_key, str(budget_used), ex=self.BALANCE_CACHE_TTL)

        # Compute reserved amount (reserves that haven't been charged or released)
        reserved = await self._compute_reserved(workflow_id)

        budget_limit = float(workflow.budget_limit)
        budget_remaining = budget_limit - budget_used
        available = budget_remaining - reserved

        return WalletBalanceResponse(
            workflow_id=workflow_id,
            budget_limit=budget_limit,
            budget_used=round(budget_used, 4),
            budget_remaining=round(max(0, budget_remaining), 4),
            reserved=round(reserved, 4),
            available=round(max(0, available), 4),
        )

    async def reserve_budget(
        self,
        workflow_id: uuid.UUID,
        agent_id: uuid.UUID,
        estimated_cost: float,
        reason: str,
        idempotency_key: str | None = None,
    ) -> WalletEvent:
        """Reserve budget before agent execution.

        Uses SELECT FOR UPDATE on the workflow row to prevent concurrent
        reservations from exceeding the budget (atomic operation).

        If an idempotency_key is provided and a WalletEvent with that key
        already exists, the existing event is returned without creating a
        duplicate.

        Raises BudgetExhaustedError if insufficient budget available.
        """
        if estimated_cost <= 0:
            raise ValidationError("Estimated cost must be positive")

        # Idempotency check: return existing event if key already used
        if idempotency_key:
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info(
                    "reserve_budget_idempotent_hit",
                    idempotency_key=idempotency_key,
                    event_id=str(existing.wallet_event_id),
                )
                return existing

        # Lock the workflow row to prevent concurrent reservation races
        result = await self.session.execute(
            select(Workflow).where(Workflow.workflow_id == workflow_id).with_for_update()
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise NotFoundError("Workflow", str(workflow_id))

        # Check available budget
        budget_used = await self._compute_balance_from_events(workflow_id)
        reserved = await self._compute_reserved(workflow_id)
        available = float(workflow.budget_limit) - budget_used - reserved

        if estimated_cost > available:
            raise BudgetExhaustedError(
                f"Insufficient budget: need {estimated_cost:.4f}, "
                f"available {available:.4f} "
                f"(limit={float(workflow.budget_limit):.4f}, "
                f"used={budget_used:.4f}, reserved={reserved:.4f})"
            )

        # Create reservation event (negative delta = funds held)
        balance_after = budget_used + estimated_cost
        event = WalletEvent(
            workflow_id=workflow_id,
            agent_id=agent_id,
            action_type=WalletActionType.RESERVE,
            credit_delta=Decimal(str(-estimated_cost)),
            balance_after=Decimal(str(balance_after)),
            reason=reason,
            metadata_={"estimated_cost": estimated_cost},
            idempotency_key=idempotency_key,
        )
        self.session.add(event)

        try:
            await self.session.flush()
        except IntegrityError:
            # Unique constraint violation on idempotency_key — concurrent insert
            await self.session.rollback()
            if idempotency_key:
                existing = await self._find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise

        await self.session.refresh(event)

        # Invalidate cache
        await self._invalidate_cache(workflow_id)

        logger.info(
            "budget_reserved",
            workflow_id=str(workflow_id),
            agent_id=str(agent_id),
            amount=estimated_cost,
            available_after=round(available - estimated_cost, 4),
        )

        await EventBus.publish_dict(
            ["wallet", str(workflow_id), "reserved"],
            "wallet.reserved",
            {
                "workflow_id": str(workflow_id),
                "agent_id": str(agent_id),
                "amount": estimated_cost,
                "event_id": str(event.wallet_event_id),
            },
        )

        return event

    async def charge(
        self,
        workflow_id: uuid.UUID,
        agent_id: uuid.UUID,
        actual_cost: float,
        reservation_event_id: uuid.UUID,
        reason: str,
        idempotency_key: str | None = None,
    ) -> WalletEvent:
        """Finalize a charge: release reservation and record actual cost.

        If actual_cost < reserved amount, the difference is credited back.
        If actual_cost > reserved amount, the additional cost is charged.

        If an idempotency_key is provided and a WalletEvent with that key
        already exists, the existing event is returned without creating a
        duplicate.
        """
        # Idempotency check: return existing event if key already used
        if idempotency_key:
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info(
                    "charge_idempotent_hit",
                    idempotency_key=idempotency_key,
                    event_id=str(existing.wallet_event_id),
                )
                return existing

        # Verify reservation exists
        res_result = await self.session.execute(
            select(WalletEvent).where(
                and_(
                    WalletEvent.wallet_event_id == reservation_event_id,
                    WalletEvent.action_type == WalletActionType.RESERVE,
                )
            )
        )
        reservation = res_result.scalar_one_or_none()
        if reservation is None:
            raise NotFoundError("Reservation", str(reservation_event_id))

        reserved_amount = abs(float(reservation.credit_delta))

        # Release the reservation
        release_event = WalletEvent(
            workflow_id=workflow_id,
            agent_id=agent_id,
            action_type=WalletActionType.RELEASE,
            credit_delta=Decimal(str(reserved_amount)),
            balance_after=Decimal("0"),  # Updated below
            reason=f"Release reservation {reservation_event_id}",
            metadata_={"reservation_event_id": str(reservation_event_id)},
        )
        self.session.add(release_event)
        await self.session.flush()  # Flush release before computing balance

        # Charge the actual cost — compute from all events (including credits)
        budget_used = await self._compute_balance_from_events(workflow_id)
        new_balance = budget_used + actual_cost

        charge_event = WalletEvent(
            workflow_id=workflow_id,
            agent_id=agent_id,
            action_type=WalletActionType.DEBIT,
            credit_delta=Decimal(str(-actual_cost)),
            balance_after=Decimal(str(new_balance)),
            reason=reason,
            metadata_={
                "reservation_event_id": str(reservation_event_id),
                "reserved_amount": reserved_amount,
                "actual_cost": actual_cost,
                "savings": round(reserved_amount - actual_cost, 4),
            },
            idempotency_key=idempotency_key,
        )
        self.session.add(charge_event)

        # Update workflow budget_used and sync ORM object
        await self.session.execute(
            update(Workflow).where(Workflow.workflow_id == workflow_id).values(budget_used=Decimal(str(new_balance)))
        )

        await self.session.flush()
        await self.session.refresh(charge_event)

        # Expire the workflow ORM object so next access re-reads from DB
        workflow_obj = await self.session.get(Workflow, workflow_id)
        if workflow_obj:
            await self.session.refresh(workflow_obj)

        # Invalidate cache
        await self._invalidate_cache(workflow_id)

        logger.info(
            "budget_charged",
            workflow_id=str(workflow_id),
            agent_id=str(agent_id),
            reserved=reserved_amount,
            actual=actual_cost,
            savings=round(reserved_amount - actual_cost, 4),
        )

        await EventBus.publish_dict(
            ["wallet", str(workflow_id), "charged"],
            "wallet.charged",
            {
                "workflow_id": str(workflow_id),
                "agent_id": str(agent_id),
                "actual_cost": actual_cost,
                "event_id": str(charge_event.wallet_event_id),
            },
        )

        return charge_event

    async def release_reservation(
        self,
        workflow_id: uuid.UUID,
        reservation_event_id: uuid.UUID,
        reason: str = "Reservation released",
        idempotency_key: str | None = None,
    ) -> WalletEvent:
        """Release a reservation without charging (e.g. task was skipped or cancelled).

        If an idempotency_key is provided and a WalletEvent with that key
        already exists, the existing event is returned without creating a
        duplicate.
        """
        # Idempotency check: return existing event if key already used
        if idempotency_key:
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info(
                    "release_reservation_idempotent_hit",
                    idempotency_key=idempotency_key,
                    event_id=str(existing.wallet_event_id),
                )
                return existing

        res_result = await self.session.execute(
            select(WalletEvent).where(
                and_(
                    WalletEvent.wallet_event_id == reservation_event_id,
                    WalletEvent.action_type == WalletActionType.RESERVE,
                )
            )
        )
        reservation = res_result.scalar_one_or_none()
        if reservation is None:
            raise NotFoundError("Reservation", str(reservation_event_id))

        reserved_amount = abs(float(reservation.credit_delta))

        release_event = WalletEvent(
            workflow_id=workflow_id,
            agent_id=reservation.agent_id,
            action_type=WalletActionType.RELEASE,
            credit_delta=Decimal(str(reserved_amount)),
            balance_after=Decimal("0"),  # Recomputed on read
            reason=reason,
            metadata_={"reservation_event_id": str(reservation_event_id)},
            idempotency_key=idempotency_key,
        )
        self.session.add(release_event)

        try:
            await self.session.flush()
        except IntegrityError:
            # Unique constraint violation on idempotency_key — concurrent insert
            await self.session.rollback()
            if idempotency_key:
                existing = await self._find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise

        await self.session.refresh(release_event)

        await self._invalidate_cache(workflow_id)

        logger.info(
            "reservation_released",
            workflow_id=str(workflow_id),
            reservation_id=str(reservation_event_id),
            amount=reserved_amount,
        )

        return release_event

    async def credit(
        self,
        workflow_id: uuid.UUID,
        amount: float,
        reason: str,
    ) -> WalletEvent:
        """Add credits to a workflow (top-up, refund, initial funding)."""
        await self._get_workflow(workflow_id)  # Validate workflow exists

        budget_used = await self._compute_balance_from_events(workflow_id)
        new_balance = max(0, budget_used - amount)

        event = WalletEvent(
            workflow_id=workflow_id,
            agent_id=None,
            action_type=WalletActionType.CREDIT,
            credit_delta=Decimal(str(amount)),
            balance_after=Decimal(str(new_balance)),
            reason=reason,
        )
        self.session.add(event)
        await self.session.flush()
        await self.session.refresh(event)

        await self._invalidate_cache(workflow_id)

        logger.info("wallet_credited", workflow_id=str(workflow_id), amount=amount)
        return event

    async def get_events(
        self,
        workflow_id: uuid.UUID,
        pagination: PaginationParams,
        action_type: WalletActionType | None = None,
    ) -> PaginatedResponse[WalletEventResponse]:
        """Get paginated wallet event history for a workflow."""
        conditions = [WalletEvent.workflow_id == workflow_id]
        if action_type:
            conditions.append(WalletEvent.action_type == action_type)

        count_result = await self.session.execute(
            select(func.count()).select_from(WalletEvent).where(and_(*conditions))
        )
        total = count_result.scalar_one()

        result = await self.session.execute(
            select(WalletEvent)
            .where(and_(*conditions))
            .order_by(WalletEvent.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
        events = list(result.scalars().all())

        return PaginatedResponse(
            items=[WalletEventResponse.model_validate(e) for e in events],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    # --- Private helpers ---

    async def _get_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        result = await self.session.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise NotFoundError("Workflow", str(workflow_id))
        return workflow

    async def _compute_balance_from_events(self, workflow_id: uuid.UUID) -> float:
        """Compute total spent from event ledger.

        budget_used = abs(sum of DEBIT deltas) - sum of CREDIT deltas.
        DEBIT deltas are negative, CREDIT deltas are positive.
        """
        result = await self.session.execute(
            select(
                func.coalesce(
                    func.sum(WalletEvent.credit_delta).filter(WalletEvent.action_type == WalletActionType.DEBIT),
                    0,
                ).label("total_debits"),
                func.coalesce(
                    func.sum(WalletEvent.credit_delta).filter(WalletEvent.action_type == WalletActionType.CREDIT),
                    0,
                ).label("total_credits"),
            ).where(WalletEvent.workflow_id == workflow_id)
        )
        row = result.one()
        # Debits are negative, credits are positive
        # budget_used = |debits| - credits
        return max(0.0, abs(float(row.total_debits)) - float(row.total_credits))

    async def _compute_reserved(self, workflow_id: uuid.UUID) -> float:
        """Compute outstanding reserved amount (reserves that haven't been released/charged).

        Outstanding = SUM(reserve amounts) - SUM(release amounts)
        """
        result = await self.session.execute(
            select(
                func.coalesce(
                    func.sum(func.abs(WalletEvent.credit_delta)).filter(
                        WalletEvent.action_type == WalletActionType.RESERVE
                    ),
                    0,
                ).label("total_reserved"),
                func.coalesce(
                    func.sum(WalletEvent.credit_delta).filter(WalletEvent.action_type == WalletActionType.RELEASE),
                    0,
                ).label("total_released"),
            ).where(WalletEvent.workflow_id == workflow_id)
        )
        row = result.one()
        outstanding = float(row.total_reserved) - float(row.total_released)
        return max(0, outstanding)

    async def _find_by_idempotency_key(self, idempotency_key: str) -> WalletEvent | None:
        """Look up a WalletEvent by its idempotency key."""
        result = await self.session.execute(select(WalletEvent).where(WalletEvent.idempotency_key == idempotency_key))
        return result.scalar_one_or_none()

    async def _invalidate_cache(self, workflow_id: uuid.UUID) -> None:
        cache_key = f"{self.BALANCE_CACHE_PREFIX}{workflow_id}"
        await self.redis.delete(cache_key)
