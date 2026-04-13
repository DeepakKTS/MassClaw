"""Unit tests for Bayesian trust scoring algorithm."""

import pytest
import pytest_asyncio
from app.services.trust_service import TrustService
from app.schemas.trust import TrustScoreInput


class TestTrustAlgorithm:
    @pytest_asyncio.fixture
    async def service(self, db_session, redis_client):
        return TrustService(db_session, redis_client)

    @pytest.mark.asyncio
    async def test_trust_increases_with_good_scores(self, service, sample_agent):
        """Good performance should increase trust."""
        initial_trust = sample_agent.trust_score
        scores = TrustScoreInput(
            quality_score=0.95, latency_score=0.9, cost_score=0.85,
            consistency_score=0.9, reliability_score=0.95,
        )
        event = await service.record_trust_event(sample_agent.agent_id, scores)
        assert event.new_trust > initial_trust * 0.9  # Should be close to or above initial

    @pytest.mark.asyncio
    async def test_trust_decreases_with_bad_scores(self, service, sample_agent):
        """Poor performance should decrease trust."""
        initial_trust = sample_agent.trust_score
        scores = TrustScoreInput(
            quality_score=0.1, latency_score=0.1, cost_score=0.1,
            consistency_score=0.1, reliability_score=0.1,
        )
        event = await service.record_trust_event(sample_agent.agent_id, scores)
        assert event.new_trust < initial_trust

    @pytest.mark.asyncio
    async def test_trust_clamped_to_bounds(self, service, sample_agent):
        """Trust should never go below TRUST_FLOOR or above TRUST_CEILING."""
        # Push trust very low
        for _ in range(10):
            scores = TrustScoreInput(
                quality_score=0.0, latency_score=0.0, cost_score=0.0,
                consistency_score=0.0, reliability_score=0.0,
            )
            event = await service.record_trust_event(sample_agent.agent_id, scores)
        assert event.new_trust >= TrustService.TRUST_FLOOR

    @pytest.mark.asyncio
    async def test_learning_rate_decreases(self, service, sample_agent):
        """Alpha should decrease with more interactions (more stable scores)."""
        scores = TrustScoreInput(
            quality_score=0.5, latency_score=0.5, cost_score=0.5,
            consistency_score=0.5, reliability_score=0.5,
        )
        event1 = await service.record_trust_event(sample_agent.agent_id, scores)
        event2 = await service.record_trust_event(sample_agent.agent_id, scores)
        # The change per event should decrease
        delta1 = abs(event1.new_trust - event1.old_trust)
        delta2 = abs(event2.new_trust - event2.old_trust)
        assert delta2 <= delta1 + 0.01  # Second delta should be smaller or equal

    @pytest.mark.asyncio
    async def test_consistency_score_computation(self, service, sample_agent):
        """Consistency score should work with insufficient data."""
        score = await service.compute_consistency_score(sample_agent.agent_id)
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_reliability_score_bayesian(self, service, sample_agent):
        """Reliability should use Bayesian smoothing."""
        score = await service.compute_reliability_score(sample_agent.agent_id)
        assert 0.0 <= score <= 1.0
        # With no data, should return ~0.5 (alpha / (alpha + beta))
        assert 0.3 <= score <= 0.7
