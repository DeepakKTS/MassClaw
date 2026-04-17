"""Unit tests for wallet service."""

import pytest
import pytest_asyncio
from decimal import Decimal
from app.services.wallet_service import WalletService
from app.exceptions import BudgetExhaustedError


class TestWalletService:
    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return WalletService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_initial_balance(self, service, sample_workflow):
        balance = await service.get_balance(sample_workflow.workflow_id)
        assert balance.budget_limit == 500.0
        assert balance.budget_used == 0.0
        assert balance.available == 500.0

    @pytest.mark.asyncio
    async def test_reserve_and_charge(self, service, sample_workflow, sample_agent):
        # Reserve
        reservation = await service.reserve_budget(
            sample_workflow.workflow_id, sample_agent.agent_id, 50.0, "test reserve"
        )
        balance = await service.get_balance(sample_workflow.workflow_id)
        assert balance.reserved == 50.0
        assert balance.available == 450.0

        # Charge less than reserved
        charge = await service.charge(
            sample_workflow.workflow_id, sample_agent.agent_id, 35.0, reservation.wallet_event_id, "test charge"
        )
        balance = await service.get_balance(sample_workflow.workflow_id)
        assert balance.budget_used == 35.0
        assert balance.reserved == 0.0

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, service, sample_workflow, sample_agent):
        with pytest.raises(BudgetExhaustedError):
            await service.reserve_budget(sample_workflow.workflow_id, sample_agent.agent_id, 600.0, "too much")

    @pytest.mark.asyncio
    async def test_release_reservation(self, service, sample_workflow, sample_agent):
        reservation = await service.reserve_budget(
            sample_workflow.workflow_id, sample_agent.agent_id, 100.0, "to release"
        )
        await service.release_reservation(sample_workflow.workflow_id, reservation.wallet_event_id, "cancelled")
        balance = await service.get_balance(sample_workflow.workflow_id)
        assert balance.reserved == 0.0
        assert balance.available == 500.0
