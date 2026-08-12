"""Tests for per-node budget estimation.

Background: a $25-budget compliance workflow reserved 12 credits for a task
whose real cost came to 64.23 — 5.35x over. The reservation was too small to
matter, but the *consequence* was not: overspend inflated ``budget_used``, the
next node's reserve raised ``BudgetExhaustedError``, and ``dag.mark_failed``
recursively SKIPped its entire descendant subtree, including the task that was
supposed to trigger the approval gate. The workflow then reported partial
success, because ``completed_count > 0``.

The old estimate was ``cost_profile["avg_cost_per_call"] * 1.5``, which is
blind to three things that dominate the real number: which model gets picked
(Haiku vs Sonnet is 3.75x), how many output tokens it is allowed, and that the
tool loop bills up to ``tool_max_iterations`` LLM calls plus tool executions as
a single node cost.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.config import get_settings
from app.llm.token_counter import count_tokens, estimate_cost, estimate_tokens
from app.models.agent import Agent
from app.models.base import AgentStatus
from app.models.workflow import Workflow
from app.orchestration.dag import DAGNode
from app.orchestration.scheduler import (
    _ASSUMED_PROMPT_TOKENS,
    HAIKU_CAPABILITIES,
    HAIKU_MODEL,
    SONNET_CAPABILITIES,
    WorkflowScheduler,
)


def _node(capability: str, complexity: str = "medium") -> DAGNode:
    return DAGNode(
        node_id="task-1",
        capability=capability,
        description="do the thing",
        estimated_complexity=complexity,
    )


def _agent_without_profile() -> Agent:
    agent = _agent()
    agent.cost_profile = None
    return agent


def _agent(avg_cost_per_call: float = 0.01) -> Agent:
    return Agent(
        agent_id=uuid.uuid4(),
        name="test-agent",
        description="test",
        capabilities=["test"],
        endpoint="internal://test",
        trust_score=0.75,
        status=AgentStatus.ACTIVE,
        cost_profile={"avg_cost_per_call": avg_cost_per_call},
    )


class TestModelSelection:
    """``_select_model`` is the single source of truth.

    Model choice used to be decided inline during execution, from capability
    sets scoped inside the execution closure, so the estimator had no way to
    ask the same question. Any drift between the two silently mis-sizes every
    reservation.
    """

    def test_haiku_capability_selects_haiku(self):
        model, max_tokens = WorkflowScheduler._select_model(_node("summarization"))
        assert "haiku" in model
        assert max_tokens > 0

    def test_high_complexity_overrides_haiku(self):
        """A hard task gets the better model even for a cheap capability."""
        model, _ = WorkflowScheduler._select_model(_node("summarization", "high"))
        assert "sonnet" in model

    def test_sonnet_capability_selects_sonnet(self):
        model, _ = WorkflowScheduler._select_model(_node(next(iter(SONNET_CAPABILITIES))))
        assert "sonnet" in model

    def test_unknown_capability_defaults_to_sonnet(self):
        model, max_tokens = WorkflowScheduler._select_model(_node("some-unheard-of-capability"))
        assert "sonnet" in model
        assert max_tokens > 0

    def test_capability_sets_are_disjoint(self):
        """Overlap would make selection depend on branch order."""
        assert not (HAIKU_CAPABILITIES & SONNET_CAPABILITIES)


class TestEstimateIsModelAware:
    def test_sonnet_node_costs_more_than_haiku_node(self):
        haiku = WorkflowScheduler._estimate_node_cost(_node("summarization"), _agent())
        sonnet = WorkflowScheduler._estimate_node_cost(_node("compliance-check", "high"), _agent())
        assert sonnet > haiku, "Sonnet is 3.75x Haiku per token; the estimate must reflect the model"

    def test_cheap_profile_cannot_drag_the_estimate_down(self):
        """A per-agent average cannot know which model a node will use, so it
        must not be what sizes the reservation. A near-zero profile — or a
        missing one — has to leave the model-derived figure intact."""
        node = _node("compliance-check", "high")
        model, max_tokens = WorkflowScheduler._select_model(node)
        one_call_credits = float(estimate_cost(_ASSUMED_PROMPT_TOKENS, max_tokens, model)) / 0.001

        near_zero = WorkflowScheduler._estimate_node_cost(node, _agent(0.0000001))
        absent = WorkflowScheduler._estimate_node_cost(node, _agent_without_profile())

        assert near_zero >= one_call_credits
        assert absent == pytest.approx(near_zero)

    def test_expensive_profile_raises_the_estimate(self):
        """Intentional floor, not a leftover. ``cost_profile`` is the only
        signal for an agent whose price is not LLM-token-based at all — an
        external paid service — so it can raise the reservation even though it
        can never lower it."""
        node = _node("compliance-check", "high")
        model_derived = WorkflowScheduler._estimate_node_cost(node, _agent(0.0000001))
        expensive = WorkflowScheduler._estimate_node_cost(node, _agent(1.0))

        assert expensive > model_derived


class TestEstimateCoversTheToolLoop:
    def test_estimate_covers_at_least_one_full_call(self):
        """A single call at the model's own max_tokens must never overrun the
        reservation on its own."""
        node = _node("compliance-check", "high")
        model, max_tokens = WorkflowScheduler._select_model(node)
        one_call_credits = float(estimate_cost(0, max_tokens, model)) / 0.001

        estimate = WorkflowScheduler._estimate_node_cost(node, _agent())

        assert estimate >= one_call_credits

    def test_estimate_scales_with_iteration_ceiling(self):
        """``_run_tool_loop`` bills up to ``tool_max_iterations`` LLM calls plus
        tool executions as one node cost. Sizing for a single call is what made
        the reservation 5x low."""
        node = _node("compliance-check", "high")
        model, max_tokens = WorkflowScheduler._select_model(node)
        one_call_credits = float(estimate_cost(0, max_tokens, model)) / 0.001

        estimate = WorkflowScheduler._estimate_node_cost(node, _agent())

        assert get_settings().tool_max_iterations > 1
        assert estimate > one_call_credits, "must allow for more than one tool-loop iteration"

    def test_estimate_beats_the_old_flat_value_for_sonnet(self):
        """The old formula gave 15 credits for the seeded 0.01 profile
        regardless of model. The observed real cost for one such node was
        64.23."""
        estimate = WorkflowScheduler._estimate_node_cost(_node("compliance-check", "high"), _agent(0.01))
        assert estimate > 15.0


class TestToolLoopBudgetGate:
    """``_can_afford_another_round`` is where the budget is actually enforced.

    The Phase 1 reservation is a heuristic pre-authorisation, and
    ``WalletService.charge`` cannot refuse anything — by the time it runs the
    money is spent, which is why it has no cap. The tool loop is the last point
    at which stopping is still free.
    """

    @staticmethod
    def _workflow(limit: float, used: float = 0.0) -> Workflow:
        return Workflow(
            workflow_id=uuid.uuid4(),
            user_id="u",
            prompt="p",
            budget_limit=limit,
            budget_used=used,
        )

    def test_fresh_workflow_can_afford_a_round(self):
        wf = self._workflow(limit=500.0)
        assert WorkflowScheduler._can_afford_another_round(wf, Decimal("0"), "claude-sonnet-5", 2000)

    def test_exhausted_workflow_cannot(self):
        wf = self._workflow(limit=10.0)
        assert not WorkflowScheduler._can_afford_another_round(wf, Decimal("0"), "claude-sonnet-5", 2000)

    def test_spend_in_the_current_node_counts_against_the_limit(self):
        """The uncharged in-flight spend has to be subtracted too, or a loop
        could spend the same headroom on every iteration."""
        wf = self._workflow(limit=100.0)
        assert WorkflowScheduler._can_afford_another_round(wf, Decimal("0"), "claude-sonnet-5", 2000)
        # 0.06 USD == 60 credits, leaving less than one ~48-credit round.
        assert not WorkflowScheduler._can_afford_another_round(wf, Decimal("0.06"), "claude-sonnet-5", 2000)

    def test_already_charged_nodes_count_against_the_limit(self):
        wf = self._workflow(limit=100.0, used=80.0)
        assert not WorkflowScheduler._can_afford_another_round(wf, Decimal("0"), "claude-sonnet-5", 2000)

    def test_cheaper_model_is_held_to_a_lower_bar(self):
        """A Haiku round costs far less, so a budget too small for Sonnet can
        still fund one."""
        wf = self._workflow(limit=20.0)
        assert not WorkflowScheduler._can_afford_another_round(wf, Decimal("0"), "claude-sonnet-5", 2000)
        assert WorkflowScheduler._can_afford_another_round(wf, Decimal("0"), HAIKU_MODEL, 1000)

    def test_reservation_leaves_room_for_the_loop_gate(self):
        """Sanity check on the calibration: the per-node reservation must not
        be so large that a demo-sized budget cannot schedule its first node.
        Harness scenario s9 submits with a 100-credit cap."""
        estimate = WorkflowScheduler._estimate_node_cost(_node("compliance-check", "high"), _agent())
        assert estimate < 100.0


class TestTokenCounter:
    def test_claude_token_count_uses_the_heuristic(self):
        """The Claude branch called ``Anthropic().count_tokens(text)``, which no
        longer exists on the SDK client (0.96 has ``messages.count_tokens``, and
        that is a network call). It therefore always threw and fell through to
        the heuristic. Assert the heuristic outright rather than pretending
        there is a tokenizer."""
        text = "a fairly ordinary sentence for counting purposes" * 10
        assert count_tokens(text, "claude-sonnet-5") == estimate_tokens(text)

    def test_claude_token_count_needs_no_api_key(self, monkeypatch):
        """It must not depend on credentials or the network."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert count_tokens("hello world", "claude-opus-5") > 0

    def test_known_models_are_priced_from_the_table(self):
        opus = estimate_cost(1000, 1000, "claude-opus-5")
        sonnet = estimate_cost(1000, 1000, "claude-sonnet-5")
        assert opus > sonnet

    def test_unknown_model_falls_back_to_default_pricing(self):
        """Documented behaviour, and a real hazard: a typo'd Opus id prices as
        Sonnet and under-bills by 5x. The fallback now logs; this pins the
        arithmetic so the risk is at least visible in the test suite."""
        unknown = estimate_cost(1000, 1000, "claude-opus-5-TYPO")
        sonnet = estimate_cost(1000, 1000, "claude-sonnet-5")
        opus = estimate_cost(1000, 1000, "claude-opus-5")
        assert unknown == sonnet
        assert unknown < opus
