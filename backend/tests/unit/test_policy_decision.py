"""Unit tests for :mod:`app.safety.decision` — Decision + aggregate."""

from __future__ import annotations

from app.safety.decision import Decision, DecisionAction, aggregate


class TestDecisionConstructors:
    def test_allow(self) -> None:
        d = Decision.allow(rule_id="r1", reason="ok", metadata={"k": "v"})
        assert d.action is DecisionAction.ALLOW
        assert d.rule_id == "r1"
        assert d.reason == "ok"
        assert d.metadata == {"k": "v"}

    def test_deny(self) -> None:
        d = Decision.deny(rule_id="r2", reason="no", metadata={"amount": 9000})
        assert d.action is DecisionAction.DENY
        assert d.metadata["amount"] == 9000

    def test_escalate_human(self) -> None:
        d = Decision.escalate_human(rule_id="r3", reason="needs human")
        assert d.action is DecisionAction.ESCALATE_HUMAN

    def test_abstain(self) -> None:
        d = Decision.abstain(rule_id="r4", reason="not my concern")
        assert d.action is DecisionAction.ABSTAIN

    def test_audit_payload_roundtrips_basics(self) -> None:
        d = Decision.deny(rule_id="rX", reason="boom", metadata={"why": "loud"})
        payload = d.to_audit_payload()
        assert payload["action"] == "deny"
        assert payload["rule_id"] == "rX"
        assert payload["reason"] == "boom"
        assert payload["metadata"] == {"why": "loud"}
        assert "created_at" in payload


class TestAggregationPrecedence:
    def test_empty_list_is_abstain(self) -> None:
        assert aggregate([]).action is DecisionAction.ABSTAIN

    def test_all_abstain_is_abstain(self) -> None:
        decisions = [Decision.abstain(rule_id="a"), Decision.abstain(rule_id="b")]
        assert aggregate(decisions).action is DecisionAction.ABSTAIN

    def test_single_allow_becomes_allow(self) -> None:
        assert aggregate([Decision.allow(rule_id="a", reason="ok")]).action is DecisionAction.ALLOW

    def test_deny_beats_allow(self) -> None:
        decisions = [
            Decision.allow(rule_id="a"),
            Decision.deny(rule_id="b", reason="policy violated"),
        ]
        result = aggregate(decisions)
        assert result.action is DecisionAction.DENY
        assert result.rule_id == "b"
        # Both rules contributed (non-abstain) → both appear in the trail.
        assert set(result.contributing_rule_ids) == {"a", "b"}

    def test_deny_beats_escalate(self) -> None:
        decisions = [
            Decision.escalate_human(rule_id="esc", reason="needs human"),
            Decision.deny(rule_id="hard", reason="nope"),
        ]
        result = aggregate(decisions)
        assert result.action is DecisionAction.DENY
        assert result.rule_id == "hard"

    def test_escalate_beats_allow(self) -> None:
        decisions = [
            Decision.allow(rule_id="a"),
            Decision.escalate_human(rule_id="esc", reason="human please"),
            Decision.abstain(rule_id="meh"),
        ]
        result = aggregate(decisions)
        assert result.action is DecisionAction.ESCALATE_HUMAN
        assert result.rule_id == "esc"
        assert set(result.contributing_rule_ids) == {"a", "esc"}
        # Abstains do NOT appear in the trail.
        assert "meh" not in result.contributing_rule_ids

    def test_multiple_denies_report_primary_plus_all_contributors(self) -> None:
        decisions = [
            Decision.deny(rule_id="first", reason="first reason", metadata={"x": 1}),
            Decision.deny(rule_id="second", reason="second reason", metadata={"y": 2}),
        ]
        result = aggregate(decisions)
        assert result.action is DecisionAction.DENY
        assert result.rule_id == "first"  # first-wins tie-break
        # Metadata is namespaced per rule.
        assert result.metadata["by_rule"]["first"] == {"x": 1}
        assert result.metadata["by_rule"]["second"] == {"y": 2}
