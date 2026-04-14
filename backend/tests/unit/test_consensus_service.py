"""Unit tests for multi-agent consensus verification service."""

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMResponse
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.services.consensus_service import ConsensusResult, ConsensusService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_response(agree: bool, confidence: float, reasoning: str = "") -> LLMResponse:
    """Build a controlled LLMResponse with a JSON verdict payload."""
    payload = json.dumps({"agree": agree, "confidence": confidence, "reasoning": reasoning})
    return LLMResponse(
        content=payload,
        input_tokens=100,
        output_tokens=50,
        model="mock",
        latency_ms=10.0,
        cost=Decimal("0.001"),
    )


async def _create_verifier(session: AsyncSession, *, trust_score: float = 0.8, suffix: str = "") -> Agent:
    """Insert an ACTIVE agent with the 'verification' capability."""
    agent = Agent(
        name=f"verifier-{suffix or uuid.uuid4().hex[:6]}",
        description="Verification-capable agent for testing",
        capabilities=["verification", "analysis"],
        endpoint="internal://test-verifier",
        trust_score=trust_score,
        status=AgentStatus.ACTIVE,
        cost_profile={"avg_cost_per_call": 0.01},
        latency_profile={"p50_ms": 200, "p95_ms": 800},
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


async def _deactivate_existing_verifiers(session: AsyncSession) -> None:
    """Deactivate any pre-seeded verification-capable agents so only
    test-created agents participate in consensus queries."""
    await session.execute(
        update(Agent)
        .where(
            Agent.status == AgentStatus.ACTIVE,
            Agent.capabilities.op("@>")(["verification"]),
        )
        .values(status=AgentStatus.INACTIVE)
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConsensusService:
    """Tests for ConsensusService.verify_with_consensus."""

    @pytest_asyncio.fixture
    async def service(self, db_session):
        return ConsensusService(db_session)

    # --- 1. Consensus reached when verifiers agree ---

    @pytest.mark.asyncio
    async def test_consensus_reached_with_agreement(self, db_session, service):
        """When all verifiers agree with high confidence the result should be agreed=True
        and weighted_agreement should exceed the consensus threshold (0.6)."""
        await _deactivate_existing_verifiers(db_session)

        # Create exactly 3 verifier agents with high trust
        for i in range(3):
            await _create_verifier(db_session, trust_score=0.9, suffix=f"agree-{i}")

        mock_response = _make_llm_response(agree=True, confidence=0.95, reasoning="Output is correct")

        with patch.object(service.router, "generate", new_callable=AsyncMock, return_value=mock_response):
            result = await service.verify_with_consensus(
                task_description="Summarise quarterly earnings",
                output="Revenue increased 12% year-over-year.",
            )

        assert isinstance(result, ConsensusResult)
        assert result.agreed is True
        assert result.weighted_agreement >= ConsensusService.CONSENSUS_THRESHOLD
        assert result.verifier_count == 3
        assert len(result.details) == 3
        # Each detail entry should report agreement
        for detail in result.details:
            assert detail["agree"] is True

    # --- 2. Consensus not reached when verifiers disagree ---

    @pytest.mark.asyncio
    async def test_consensus_not_reached(self, db_session, service):
        """When all verifiers disagree the result should be agreed=False."""
        await _deactivate_existing_verifiers(db_session)

        for i in range(3):
            await _create_verifier(db_session, trust_score=0.85, suffix=f"disagree-{i}")

        mock_response = _make_llm_response(
            agree=False, confidence=0.9, reasoning="Output contains factual errors"
        )

        with patch.object(service.router, "generate", new_callable=AsyncMock, return_value=mock_response):
            result = await service.verify_with_consensus(
                task_description="Explain quantum computing",
                output="Quantum computers use magic crystals.",
            )

        assert result.agreed is False
        assert result.weighted_agreement < ConsensusService.CONSENSUS_THRESHOLD
        assert result.verifier_count == 3
        for detail in result.details:
            assert detail["agree"] is False

    # --- 3. Graceful degradation with no verifiers ---

    @pytest.mark.asyncio
    async def test_no_verifiers_available(self, db_session, service):
        """When no verification-capable agents exist the service should return a
        default result (agreed=True, verifier_count=0) without crashing."""
        await _deactivate_existing_verifiers(db_session)

        result = await service.verify_with_consensus(
            task_description="Some task",
            output="Some output",
        )

        assert isinstance(result, ConsensusResult)
        assert result.agreed is True
        assert result.weighted_agreement == 1.0
        assert result.verifier_count == 0
        assert result.details == []

    # --- 4. Weighted agreement calculation correctness ---

    @pytest.mark.asyncio
    async def test_weighted_agreement_calculation(self, db_session, service):
        """Verify the trust-weighted voting arithmetic.

        Setup:
          Verifier A: trust=0.9, agree=True,  confidence=1.0 => weight=0.9
          Verifier B: trust=0.6, agree=False, confidence=0.8 => weight=0.48
          Verifier C: trust=0.7, agree=True,  confidence=0.5 => weight=0.35

        weighted_agree  = 0.9 + 0.35 = 1.25
        total_weight    = 0.9 + 0.48 + 0.35 = 1.73
        agreement_ratio = 1.25 / 1.73 ≈ 0.7225  (> 0.6 threshold => agreed)
        """
        await _deactivate_existing_verifiers(db_session)

        agent_a = await _create_verifier(db_session, trust_score=0.9, suffix="calc-a")
        agent_b = await _create_verifier(db_session, trust_score=0.6, suffix="calc-b")
        agent_c = await _create_verifier(db_session, trust_score=0.7, suffix="calc-c")

        # Map each agent_id to a specific LLM response
        responses = {
            str(agent_a.agent_id): _make_llm_response(agree=True, confidence=1.0),
            str(agent_b.agent_id): _make_llm_response(agree=False, confidence=0.8),
            str(agent_c.agent_id): _make_llm_response(agree=True, confidence=0.5),
        }

        # Query orders by trust_score DESC: 0.9, 0.7, 0.6
        ordered_ids = [str(agent_a.agent_id), str(agent_c.agent_id), str(agent_b.agent_id)]
        response_queue = [responses[aid] for aid in ordered_ids]
        call_idx = {"i": 0}

        async def _mock_generate(**kwargs):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return response_queue[idx]

        with patch.object(service.router, "generate", side_effect=_mock_generate):
            result = await service.verify_with_consensus(
                task_description="Verify calculation",
                output="2+2=4",
                verifier_count=3,
            )

        # Expected math
        # Agent A (trust=0.9): agree=True, conf=1.0  => weight = 0.9
        # Agent C (trust=0.7): agree=True, conf=0.5  => weight = 0.35
        # Agent B (trust=0.6): agree=False, conf=0.8 => weight = 0.48
        expected_weighted_agree = 0.9 + 0.35  # 1.25
        expected_total_weight = 0.9 + 0.35 + 0.48  # 1.73
        expected_ratio = round(expected_weighted_agree / expected_total_weight, 4)

        assert result.agreed is True  # 0.7225 >= 0.6
        assert result.weighted_agreement == expected_ratio
        assert result.verifier_count == 3
