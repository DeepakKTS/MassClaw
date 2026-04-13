"""Unit tests for policy engine condition evaluator."""

import pytest
from app.safety.policy_engine import PolicyEngine


class TestConditionEvaluator:
    def _engine(self):
        return PolicyEngine.__new__(PolicyEngine)

    def test_simple_lt(self):
        e = self._engine()
        ctx = {"agent": {"trust_score": 0.1}}
        assert e._evaluate_condition({"field": "agent.trust_score", "op": "lt", "value": 0.3}, ctx) is True
        assert e._evaluate_condition({"field": "agent.trust_score", "op": "lt", "value": 0.05}, ctx) is False

    def test_simple_gt(self):
        e = self._engine()
        ctx = {"cost": 150}
        assert e._evaluate_condition({"field": "cost", "op": "gt", "value": 100}, ctx) is True

    def test_eq(self):
        e = self._engine()
        ctx = {"status": "active"}
        assert e._evaluate_condition({"field": "status", "op": "eq", "value": "active"}, ctx) is True

    def test_in_operator(self):
        e = self._engine()
        ctx = {"role": "admin"}
        assert e._evaluate_condition({"field": "role", "op": "in", "value": ["admin", "superadmin"]}, ctx) is True

    def test_all_combinator(self):
        e = self._engine()
        ctx = {"agent": {"trust_score": 0.1}, "cost": 200}
        cond = {"all": [
            {"field": "agent.trust_score", "op": "lt", "value": 0.3},
            {"field": "cost", "op": "gt", "value": 100},
        ]}
        assert e._evaluate_condition(cond, ctx) is True

    def test_any_combinator(self):
        e = self._engine()
        ctx = {"agent": {"trust_score": 0.9}, "cost": 200}
        cond = {"any": [
            {"field": "agent.trust_score", "op": "lt", "value": 0.3},
            {"field": "cost", "op": "gt", "value": 100},
        ]}
        assert e._evaluate_condition(cond, ctx) is True

    def test_not_combinator(self):
        e = self._engine()
        ctx = {"agent": {"trust_score": 0.9}}
        cond = {"not": {"field": "agent.trust_score", "op": "lt", "value": 0.3}}
        assert e._evaluate_condition(cond, ctx) is True

    def test_nested_boolean(self):
        e = self._engine()
        ctx = {"agent": {"trust_score": 0.1, "status": "active"}, "cost": 50}
        cond = {"all": [
            {"field": "agent.trust_score", "op": "lt", "value": 0.3},
            {"any": [
                {"field": "cost", "op": "gt", "value": 100},
                {"field": "agent.status", "op": "eq", "value": "active"},
            ]}
        ]}
        assert e._evaluate_condition(cond, ctx) is True

    def test_missing_field_returns_false(self):
        e = self._engine()
        ctx = {"agent": {}}
        assert e._evaluate_condition({"field": "agent.missing", "op": "lt", "value": 0.3}, ctx) is False

    def test_resolve_field_dot_notation(self):
        e = self._engine()
        found, val = e._resolve_field({"a": {"b": {"c": 42}}}, "a.b.c")
        assert found is True
        assert val == 42
