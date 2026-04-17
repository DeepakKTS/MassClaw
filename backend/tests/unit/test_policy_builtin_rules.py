"""Unit tests for the ten Phase-1 built-in policy rules.

Each rule is called directly with a hand-built :class:`PolicyContext`
so the tests don't depend on the registry being in any particular
state. Decorator-driven registration is covered in
``test_policy_registry.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.safety.context import PolicyContext
from app.safety.decision import DecisionAction
from app.safety.rules import (
    cost_caps as r_cost,
)
from app.safety.rules import (
    credential_requires as r_cred,
)
from app.safety.rules import (
    cross_agent_delegation as r_deleg,
)
from app.safety.rules import (
    default_deny_unknown_tool as r_default,
)
from app.safety.rules import (
    memory_write_attribution as r_attr,
)
from app.safety.rules import (
    pii_tool_guards as r_pii,
)
from app.safety.rules import (
    rate_limit_per_tool as r_rate,
)
from app.safety.rules import (
    signature_requires as r_sig,
)
from app.safety.rules import (
    time_of_day_restrictions as r_tod,
)
from app.safety.rules import (
    trust_floor_for_payments as r_trust,
)


def _ctx(**overrides) -> PolicyContext:
    defaults = dict(
        agent_did="did:key:z6MkTest",
        agent_trust_score=0.7,
        agent_capabilities=("research",),
        agent_id=uuid.uuid4(),
        action="execute_tool",
        action_category=None,
        tool_name=None,
        amount=None,
        target=None,
        estimated_cost=None,
        workflow_id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        now=datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


# -----------------------------------------------------------------------------
# trust_floor_for_payments
# -----------------------------------------------------------------------------


class TestTrustFloorForPayments:
    @pytest.mark.asyncio
    async def test_non_payment_abstains(self) -> None:
        d = await r_trust.trust_floor_for_payments(_ctx(action="execute_tool", amount=50))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_high_trust_allows(self) -> None:
        d = await r_trust.trust_floor_for_payments(_ctx(action_category="payment", amount=100, agent_trust_score=0.8))
        assert d.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_mid_trust_escalates(self) -> None:
        d = await r_trust.trust_floor_for_payments(_ctx(action_category="payment", amount=100, agent_trust_score=0.5))
        assert d.action is DecisionAction.ESCALATE_HUMAN

    @pytest.mark.asyncio
    async def test_low_trust_denies(self) -> None:
        d = await r_trust.trust_floor_for_payments(_ctx(action_category="payment", amount=100, agent_trust_score=0.1))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_missing_trust_denies(self) -> None:
        d = await r_trust.trust_floor_for_payments(_ctx(action_category="payment", amount=100, agent_trust_score=None))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_action_prefix_triggers(self) -> None:
        d = await r_trust.trust_floor_for_payments(_ctx(action="pay_vendor", amount=100, agent_trust_score=0.1))
        assert d.action is DecisionAction.DENY


# -----------------------------------------------------------------------------
# rate_limit_per_tool
# -----------------------------------------------------------------------------


class _FakeRedisPipeline:
    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self._results: list = []

    def zremrangebyscore(self, *args, **kwargs):
        self._results.append(0)
        return self

    def zcard(self, *args, **kwargs):
        self._results.append(self._counts.pop(0) if self._counts else 0)
        return self

    def zadd(self, *args, **kwargs):
        self._results.append(1)
        return self

    def expire(self, *args, **kwargs):
        self._results.append(True)
        return self

    async def execute(self):
        return self._results

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRedis:
    def __init__(self, pre_insert_counts: list[int]) -> None:
        self._counts = pre_insert_counts

    def pipeline(self, transaction=True):
        return _FakeRedisPipeline(self._counts)


class TestRateLimitPerTool:
    @pytest.mark.asyncio
    async def test_no_tool_abstains(self) -> None:
        d = await r_rate.rate_limit_per_tool(_ctx(tool_name=None))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_no_redis_abstains(self) -> None:
        d = await r_rate.rate_limit_per_tool(_ctx(tool_name="web_search"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_under_capacity_allows(self) -> None:
        # pre-insert count 5 → after insert 6, capacity 20 → allow.
        d = await r_rate.rate_limit_per_tool(_ctx(tool_name="web_search", redis=_FakeRedis([5])))
        assert d.action is DecisionAction.ALLOW
        assert d.metadata["count_in_window"] == 6

    @pytest.mark.asyncio
    async def test_over_capacity_denies(self) -> None:
        # pre-insert count 20 → after insert 21, capacity 20 → deny.
        d = await r_rate.rate_limit_per_tool(_ctx(tool_name="web_search", redis=_FakeRedis([20])))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_custom_capacity_override(self) -> None:
        d = await r_rate.rate_limit_per_tool(
            _ctx(
                tool_name="web_search",
                redis=_FakeRedis([3]),
                extra={"rate_limit_capacity": 3, "rate_limit_window_seconds": 60},
            )
        )
        # pre 3 → after 4, capacity 3 → deny
        assert d.action is DecisionAction.DENY


# -----------------------------------------------------------------------------
# cost_caps
# -----------------------------------------------------------------------------


class TestCostCaps:
    @pytest.mark.asyncio
    async def test_no_cost_abstains(self) -> None:
        d = await r_cost.cost_caps(_ctx(estimated_cost=None))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_under_threshold_allows(self) -> None:
        d = await r_cost.cost_caps(_ctx(estimated_cost=1.0))
        assert d.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_between_thresholds_escalates(self) -> None:
        d = await r_cost.cost_caps(_ctx(estimated_cost=15.0))
        assert d.action is DecisionAction.ESCALATE_HUMAN

    @pytest.mark.asyncio
    async def test_over_hard_cap_denies(self) -> None:
        d = await r_cost.cost_caps(_ctx(estimated_cost=100.0))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_overrides_honoured(self) -> None:
        # Lower escalate_at so a small cost escalates.
        d = await r_cost.cost_caps(_ctx(estimated_cost=2.0, extra={"cost_escalate_at": 1.0, "cost_deny_at": 5.0}))
        assert d.action is DecisionAction.ESCALATE_HUMAN


# -----------------------------------------------------------------------------
# time_of_day_restrictions
# -----------------------------------------------------------------------------


class TestTimeOfDayRestrictions:
    @pytest.mark.asyncio
    async def test_non_sensitive_abstains(self) -> None:
        d = await r_tod.time_of_day_restrictions(_ctx(action_category="research"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_in_hours_allows(self) -> None:
        d = await r_tod.time_of_day_restrictions(
            _ctx(action_category="payment", now=datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC))
        )
        assert d.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_out_of_hours_escalates(self) -> None:
        d = await r_tod.time_of_day_restrictions(
            _ctx(action_category="payment", now=datetime(2026, 5, 2, 22, 30, 0, tzinfo=UTC))
        )
        assert d.action is DecisionAction.ESCALATE_HUMAN


# -----------------------------------------------------------------------------
# pii_tool_guards
# -----------------------------------------------------------------------------


class TestPiiToolGuards:
    @pytest.mark.asyncio
    async def test_non_pii_abstains(self) -> None:
        d = await r_pii.pii_tool_guards(_ctx(tool_name="web_search"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_pii_tool_escalates(self) -> None:
        d = await r_pii.pii_tool_guards(_ctx(tool_name="lookup_ssn"))
        assert d.action is DecisionAction.ESCALATE_HUMAN

    @pytest.mark.asyncio
    async def test_category_only_escalates(self) -> None:
        d = await r_pii.pii_tool_guards(_ctx(tool_name="internal_fetch", action_category="pii_access"))
        assert d.action is DecisionAction.ESCALATE_HUMAN


# -----------------------------------------------------------------------------
# memory_write_attribution
# -----------------------------------------------------------------------------


class TestMemoryWriteAttribution:
    @pytest.mark.asyncio
    async def test_non_memory_write_abstains(self) -> None:
        d = await r_attr.memory_write_attribution(_ctx(action="execute_tool"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_valid_did_allows(self) -> None:
        d = await r_attr.memory_write_attribution(_ctx(action="write_memory", agent_did="did:key:z6MkValid"))
        assert d.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_missing_did_denies(self) -> None:
        d = await r_attr.memory_write_attribution(_ctx(action="write_memory", agent_did=None))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_bogus_did_denies(self) -> None:
        d = await r_attr.memory_write_attribution(_ctx(action="write_memory", agent_did="not-a-did"))
        assert d.action is DecisionAction.DENY


# -----------------------------------------------------------------------------
# cross_agent_delegation
# -----------------------------------------------------------------------------


class TestCrossAgentDelegation:
    @pytest.mark.asyncio
    async def test_non_delegation_abstains(self) -> None:
        d = await r_deleg.cross_agent_delegation(_ctx(action="execute_tool"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_shallow_delegation_allows(self) -> None:
        d = await r_deleg.cross_agent_delegation(_ctx(action="delegate_task", extra={"delegation_depth": 1}))
        assert d.action is DecisionAction.ALLOW

    @pytest.mark.asyncio
    async def test_deep_delegation_escalates(self) -> None:
        d = await r_deleg.cross_agent_delegation(_ctx(action="delegate_task", extra={"delegation_depth": 5}))
        assert d.action is DecisionAction.ESCALATE_HUMAN


# -----------------------------------------------------------------------------
# credential_requires
# -----------------------------------------------------------------------------


class TestCredentialRequires:
    """Phase-1 credential gate is OPT-IN via ``extra.require_credential``.

    A bare payment with no opt-in must not trigger the rule — that's
    what lets trust-floor and cost-cap escalation paths run cleanly
    without a credential attached. Turning the rule on requires
    ``require_credential=true`` in the policy context's extra bag.
    """

    @pytest.mark.asyncio
    async def test_non_sensitive_abstains(self) -> None:
        d = await r_cred.credential_requires(_ctx(action_category="research"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_sensitive_without_optin_abstains(self) -> None:
        # Phase-1: no require_credential flag → the rule is inert.
        d = await r_cred.credential_requires(_ctx(action_category="payment"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_opted_in_missing_credential_denies(self) -> None:
        d = await r_cred.credential_requires(_ctx(action_category="payment", extra={"require_credential": True}))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_expired_credential_denies(self) -> None:
        now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
        expired = (now - timedelta(hours=1)).isoformat()
        d = await r_cred.credential_requires(
            _ctx(
                action_category="payment",
                now=now,
                extra={
                    "require_credential": True,
                    "credential": {"expires_at": expired},
                },
            )
        )
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_fresh_credential_allows(self) -> None:
        now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
        expires = (now + timedelta(hours=1)).isoformat()
        d = await r_cred.credential_requires(
            _ctx(
                action_category="payment",
                now=now,
                extra={
                    "require_credential": True,
                    "credential": {"expires_at": expires},
                },
            )
        )
        assert d.action is DecisionAction.ALLOW


# -----------------------------------------------------------------------------
# signature_requires
# -----------------------------------------------------------------------------


class TestSignatureRequires:
    @pytest.mark.asyncio
    async def test_non_memory_abstains(self) -> None:
        d = await r_sig.signature_requires(_ctx(action="execute_tool"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_missing_signature_denies(self) -> None:
        d = await r_sig.signature_requires(_ctx(action="write_memory"))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_blank_signature_denies(self) -> None:
        d = await r_sig.signature_requires(_ctx(action="write_memory", extra={"signature": "   "}))
        assert d.action is DecisionAction.DENY

    @pytest.mark.asyncio
    async def test_valid_signature_allows(self) -> None:
        d = await r_sig.signature_requires(_ctx(action="write_memory", extra={"signature": "z3KmValidSig..."}))
        assert d.action is DecisionAction.ALLOW


# -----------------------------------------------------------------------------
# default_deny_unknown_tool
# -----------------------------------------------------------------------------


class TestDefaultDenyUnknownTool:
    @pytest.mark.asyncio
    async def test_no_tool_abstains(self) -> None:
        d = await r_default.default_deny_unknown_tool(_ctx(tool_name=None))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_no_allowlist_abstains(self) -> None:
        d = await r_default.default_deny_unknown_tool(_ctx(tool_name="web_search"))
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_known_tool_abstains(self) -> None:
        d = await r_default.default_deny_unknown_tool(
            _ctx(tool_name="web_search", extra={"known_tools": ["web_search", "http_get"]})
        )
        assert d.action is DecisionAction.ABSTAIN

    @pytest.mark.asyncio
    async def test_unknown_tool_denies(self) -> None:
        d = await r_default.default_deny_unknown_tool(
            _ctx(tool_name="destroy_everything", extra={"known_tools": ["web_search"]})
        )
        assert d.action is DecisionAction.DENY


# -----------------------------------------------------------------------------
# package-level sanity check
# -----------------------------------------------------------------------------


def test_builtin_rule_ids_has_ten_entries() -> None:
    from app.safety.rules import BUILTIN_RULE_IDS

    assert len(BUILTIN_RULE_IDS) == 10
    # IDs are unique — duplicates would mean two rules collided.
    assert len(set(BUILTIN_RULE_IDS)) == 10
