from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.dependencies import get_wallet_service
from app.models.base import WalletActionType
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.wallet import (
    WalletBalanceResponse,
    WalletEventResponse,
)
from app.services.wallet_service import WalletService

router = APIRouter()


# --- Request schemas specific to API layer ---


class ReserveRequest(BaseModel):
    agent_id: uuid.UUID
    estimated_cost: float = Field(..., gt=0)
    reason: str = Field(..., max_length=500)


class ChargeRequest(BaseModel):
    agent_id: uuid.UUID
    actual_cost: float = Field(..., gt=0)
    reservation_event_id: uuid.UUID
    reason: str = Field(..., max_length=500)


class ReleaseRequest(BaseModel):
    reservation_event_id: uuid.UUID
    reason: str = Field(default="Reservation released", max_length=500)


class CreditRequest(BaseModel):
    amount: float = Field(..., gt=0)
    reason: str = Field(..., max_length=500)


# --- Endpoints ---


@router.get("/workflow/{workflow_id}/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance(
    workflow_id: uuid.UUID,
    service: WalletService = Depends(get_wallet_service),
) -> WalletBalanceResponse:
    """Get current wallet balance, including reserved and available amounts."""
    return await service.get_balance(workflow_id)


@router.get("/workflow/{workflow_id}/events", response_model=PaginatedResponse[WalletEventResponse])
async def get_wallet_events(
    workflow_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    action_type: WalletActionType | None = Query(default=None),
    service: WalletService = Depends(get_wallet_service),
) -> PaginatedResponse[WalletEventResponse]:
    """Get paginated wallet event history for a workflow."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.get_events(workflow_id, pagination, action_type=action_type)


@router.post("/workflow/{workflow_id}/reserve", response_model=WalletEventResponse, status_code=201)
async def reserve_budget(
    workflow_id: uuid.UUID,
    data: ReserveRequest,
    service: WalletService = Depends(get_wallet_service),
) -> WalletEventResponse:
    """Reserve budget before agent execution.

    Atomic operation using row-level locking to prevent concurrent
    reservations from exceeding the budget. Raises 402 if insufficient funds.
    """
    event = await service.reserve_budget(
        workflow_id=workflow_id,
        agent_id=data.agent_id,
        estimated_cost=data.estimated_cost,
        reason=data.reason,
    )
    return WalletEventResponse.model_validate(event)


@router.post("/workflow/{workflow_id}/charge", response_model=WalletEventResponse, status_code=201)
async def charge_budget(
    workflow_id: uuid.UUID,
    data: ChargeRequest,
    service: WalletService = Depends(get_wallet_service),
) -> WalletEventResponse:
    """Finalize a charge: release reservation and record actual cost.

    If actual < reserved, the savings are returned to available budget.
    """
    event = await service.charge(
        workflow_id=workflow_id,
        agent_id=data.agent_id,
        actual_cost=data.actual_cost,
        reservation_event_id=data.reservation_event_id,
        reason=data.reason,
    )
    return WalletEventResponse.model_validate(event)


@router.post("/workflow/{workflow_id}/release", response_model=WalletEventResponse, status_code=201)
async def release_reservation(
    workflow_id: uuid.UUID,
    data: ReleaseRequest,
    service: WalletService = Depends(get_wallet_service),
) -> WalletEventResponse:
    """Release a reservation without charging (task skipped or cancelled)."""
    event = await service.release_reservation(
        workflow_id=workflow_id,
        reservation_event_id=data.reservation_event_id,
        reason=data.reason,
    )
    return WalletEventResponse.model_validate(event)


@router.post("/workflow/{workflow_id}/credit", response_model=WalletEventResponse, status_code=201)
async def credit_workflow(
    workflow_id: uuid.UUID,
    data: CreditRequest,
    service: WalletService = Depends(get_wallet_service),
) -> WalletEventResponse:
    """Add credits to a workflow (top-up, refund, initial funding)."""
    event = await service.credit(
        workflow_id=workflow_id,
        amount=data.amount,
        reason=data.reason,
    )
    return WalletEventResponse.model_validate(event)
