"""Unit tests for :mod:`app.safety.registry` — decorator, registry, evaluator."""

from __future__ import annotations

import uuid

import pytest

from app.safety.context import PolicyContext
from app.safety.decision import Decision, DecisionAction
from app.safety.registry import (
    PolicyRegistry,
    PolicyRuleEntry,
    evaluate,
    policy_rule,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Every test starts with an empty registry — prevents cross-test leakage."""
    PolicyRegistry.clear()
    yield
    PolicyRegistry.clear()


def _ctx(**overrides) -> PolicyContext:
    defaults = {
        "agent_did": "did:key:z6Mktest",
        "agent_trust_score": 0.8,
        "agent_capabilities": ("research", "writing"),
        "action": "execute_tool",
        "tool_name": "web_search",
        "workflow_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return PolicyContext(**defaults)


class TestDecoratorRegistration:
    def test_decorator_registers_rule(self) -> None:
        @policy_rule(rule_id="my_rule", description="test rule", priority=5, tags=("test",))
        async def my_rule(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="my_rule")

        entry = PolicyRegistry.get("my_rule")
        assert entry is not None
        assert entry.description == "test rule"
        assert entry.priority == 5
        assert entry.tags == ("test",)
        assert entry.enabled is True

    def test_decorator_wraps_sync_function(self) -> None:
        @policy_rule(rule_id="sync_rule")
        def sync_rule(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="sync_rule", reason="sync-ok")

        entry = PolicyRegistry.get("sync_rule")
        assert entry is not None
        # The stored function is always async after decoration.
        import asyncio

        result = asyncio.run(entry.function(_ctx()))
        assert result.action is DecisionAction.ALLOW
        assert result.reason == "sync-ok"

    def test_redefining_replaces_entry(self) -> None:
        @policy_rule(rule_id="dup")
        async def first(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="dup", reason="first")

        @policy_rule(rule_id="dup")
        async def second(ctx: PolicyContext) -> Decision:
            return Decision.deny(rule_id="dup", reason="second")

        # Only one entry — the later one wins.
        entries = PolicyRegistry.all(include_disabled=True)
        assert len(entries) == 1


class TestRegistryToggles:
    def test_set_enabled_flips_flag(self) -> None:
        @policy_rule(rule_id="toggleable")
        async def rule(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="toggleable")

        assert PolicyRegistry.set_enabled("toggleable", False) is True
        entry = PolicyRegistry.get("toggleable")
        assert entry is not None and entry.enabled is False

        # Disabled rules don't appear in ``all()`` by default.
        assert PolicyRegistry.all() == []
        assert len(PolicyRegistry.all(include_disabled=True)) == 1

    def test_set_enabled_unknown_id_returns_false(self) -> None:
        assert PolicyRegistry.set_enabled("ghost", True) is False

    def test_unregister(self) -> None:
        @policy_rule(rule_id="removeme")
        async def rule(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="removeme")

        assert PolicyRegistry.unregister("removeme") is True
        assert PolicyRegistry.get("removeme") is None
        assert PolicyRegistry.unregister("never_there") is False


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_single_allow_returns_allow(self) -> None:
        @policy_rule(rule_id="just_allow")
        async def rule(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="just_allow", reason="fine")

        result = await evaluate(_ctx())
        assert result.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_deny_wins_and_short_circuits(self) -> None:
        call_log = []

        @policy_rule(rule_id="denier", priority=1)
        async def denier(ctx: PolicyContext) -> Decision:
            call_log.append("denier")
            return Decision.deny(rule_id="denier", reason="nope")

        @policy_rule(rule_id="later", priority=2)
        async def later(ctx: PolicyContext) -> Decision:  # should not be called
            call_log.append("later")
            return Decision.allow(rule_id="later")

        result = await evaluate(_ctx())
        assert result.action is DecisionAction.DENY
        # short-circuit: the later rule never ran.
        assert call_log == ["denier"]

    @pytest.mark.asyncio
    async def test_escalate_plus_allow_becomes_escalate(self) -> None:
        @policy_rule(rule_id="a_allow", priority=1)
        async def a(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="a_allow")

        @policy_rule(rule_id="b_escalate", priority=2)
        async def b(ctx: PolicyContext) -> Decision:
            return Decision.escalate_human(rule_id="b_escalate", reason="need human")

        result = await evaluate(_ctx())
        assert result.action is DecisionAction.ESCALATE_HUMAN

    @pytest.mark.asyncio
    async def test_abstaining_rules_do_not_block(self) -> None:
        @policy_rule(rule_id="abstainer")
        async def rule(ctx: PolicyContext) -> Decision:
            return Decision.abstain(rule_id="abstainer")

        result = await evaluate(_ctx())
        assert result.action is DecisionAction.ABSTAIN  # empty non-abstain set

    @pytest.mark.asyncio
    async def test_crashing_rule_becomes_abstain_and_doesnt_kill_eval(self) -> None:
        @policy_rule(rule_id="crasher", priority=1)
        async def crasher(ctx: PolicyContext) -> Decision:
            raise RuntimeError("boom")

        @policy_rule(rule_id="survivor", priority=2)
        async def survivor(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="survivor", reason="I survived")

        result = await evaluate(_ctx())
        assert result.action is DecisionAction.ALLOW
        assert result.rule_id == "survivor"

    @pytest.mark.asyncio
    async def test_non_decision_return_becomes_abstain(self) -> None:
        @policy_rule(rule_id="buggy")
        async def buggy(ctx: PolicyContext) -> Decision:  # type: ignore[return-value]
            return "not a decision"  # type: ignore[return-value]

        result = await evaluate(_ctx())
        assert result.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_rule_ids_filter(self) -> None:
        @policy_rule(rule_id="a")
        async def a(ctx: PolicyContext) -> Decision:
            return Decision.deny(rule_id="a", reason="a says no")

        @policy_rule(rule_id="b")
        async def b(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="b")

        # Only evaluate b — the deny from a should not fire.
        result = await evaluate(_ctx(), rule_ids=["b"])
        assert result.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_disabled_rule_is_not_evaluated_by_default(self) -> None:
        @policy_rule(rule_id="disabled_denier", priority=1)
        async def disabled_denier(ctx: PolicyContext) -> Decision:
            return Decision.deny(rule_id="disabled_denier", reason="would block")

        @policy_rule(rule_id="always_allow", priority=2)
        async def always_allow(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="always_allow")

        PolicyRegistry.set_enabled("disabled_denier", False)
        result = await evaluate(_ctx())
        assert result.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_priority_orders_evaluation(self) -> None:
        order: list[str] = []

        @policy_rule(rule_id="high", priority=10)
        async def high(ctx: PolicyContext) -> Decision:
            order.append("high")
            return Decision.abstain(rule_id="high")

        @policy_rule(rule_id="low", priority=1)
        async def low(ctx: PolicyContext) -> Decision:
            order.append("low")
            return Decision.abstain(rule_id="low")

        await evaluate(_ctx())
        # Low priority runs first (asc).
        assert order == ["low", "high"]


class TestBareEntry:
    def test_manual_register_entry(self) -> None:
        async def bare(ctx: PolicyContext) -> Decision:
            return Decision.allow(rule_id="bare")

        PolicyRegistry.register(PolicyRuleEntry(rule_id="bare", description="manual", function=bare, priority=5))
        entry = PolicyRegistry.get("bare")
        assert entry is not None
        assert entry.description == "manual"
