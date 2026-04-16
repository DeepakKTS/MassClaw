"""Unit tests for the three-mode CRDT fact resolver.

These are pure-Python tests — no database, no HTTP. The resolver takes a
list of :class:`FactCandidate` values (which the service layer assembles
from DB rows + agent trust scores) and produces a :class:`FactResolution`.
Covering this layer thoroughly means we can trust the behaviour no matter
how messy the upstream data is.

The canonical scenario under test is the "three deadlines" example from
the design spec:

- Gamma wrote ``Apr 28`` offline, trust 0.71, confidence 0.85.
- Alpha wrote ``Apr 30``, trust 0.93, confidence 0.9.
- Beta wrote ``May 15``, trust 0.88, confidence 0.88.

Planning mode → Alpha wins.
Audit mode → all three ranked.
Sensitive mode → HITL because Alpha vs Beta are close in rank.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.base import ReadMode, RecordState
from app.services.memory_fact_resolver import (
    FactCandidate,
    resolve_fact,
)


# ---------------------------------------------------------------- fixtures


def _candidate(
    content: str,
    *,
    confidence: float = 0.9,
    author_did: str | None = "did:key:z6Mk-gamma",
    author_trust: float | None = 0.5,
    record_state: RecordState = RecordState.ACTIVE,
    content_hash: str | None = None,
    created_at: datetime | None = None,
    similarity: float = 0.92,
    memory_id: uuid.UUID | None = None,
) -> FactCandidate:
    return FactCandidate(
        memory_id=memory_id or uuid.uuid4(),
        content=content,
        confidence=confidence,
        author_did=author_did,
        author_trust=author_trust,
        record_state=record_state,
        content_hash=content_hash or f"z{content.replace(' ', '')[:40]:<40}",
        created_at=created_at or datetime.now(UTC),
        similarity=similarity,
    )


def _deadlines(now: datetime) -> list[FactCandidate]:
    """Canonical three-deadlines scenario."""
    return [
        _candidate(
            "Deadline: April 28",
            confidence=0.85,
            author_did="did:key:z6Mk-gamma",
            author_trust=0.71,
            created_at=now - timedelta(hours=4),
            content_hash="zGAMMA" + "a" * 40,
        ),
        _candidate(
            "Deadline: April 30",
            confidence=0.90,
            author_did="did:key:z6Mk-alpha",
            author_trust=0.93,
            created_at=now - timedelta(hours=2),
            content_hash="zALPHA" + "a" * 40,
        ),
        _candidate(
            "Deadline: May 15",
            confidence=0.88,
            author_did="did:key:z6Mk-beta",
            author_trust=0.88,
            created_at=now - timedelta(hours=1),
            content_hash="zBETAA" + "a" * 40,
        ),
    ]


# ---------------------------------------------------------------- planning mode


class TestPlanningMode:
    def test_picks_highest_rank_candidate(self) -> None:
        now = datetime.now(UTC)
        result = resolve_fact(_deadlines(now), mode=ReadMode.PLANNING, now=now)
        assert result.chosen is not None
        assert result.chosen.content == "Deadline: April 30"
        assert len(result.candidates) == 3
        assert "top-ranked" in result.reason

    def test_empty_input_returns_none_chosen(self) -> None:
        result = resolve_fact([], mode=ReadMode.PLANNING)
        assert result.chosen is None
        assert result.candidates == []
        assert result.requires_hitl is False
        assert "no active candidates" in result.reason

    def test_respects_record_state_filter(self) -> None:
        """Only ACTIVE records are eligible for planning."""
        now = datetime.now(UTC)
        candidates = [
            _candidate("high", confidence=0.99, record_state=RecordState.SUPERSEDED, created_at=now),
            _candidate("lower", confidence=0.6, record_state=RecordState.ACTIVE, created_at=now),
        ]
        result = resolve_fact(candidates, mode=ReadMode.PLANNING, now=now)
        assert result.chosen is not None
        assert result.chosen.content == "lower"

    def test_unknown_author_trust_uses_default(self) -> None:
        now = datetime.now(UTC)
        candidates = [
            _candidate("A", confidence=0.9, author_trust=None, created_at=now),
            _candidate("B", confidence=0.5, author_trust=None, created_at=now),
        ]
        result = resolve_fact(candidates, mode=ReadMode.PLANNING, now=now)
        # Both have same unknown trust → higher confidence wins.
        assert result.chosen is not None
        assert result.chosen.content == "A"

    def test_sorted_candidate_list_is_desc_by_rank(self) -> None:
        now = datetime.now(UTC)
        result = resolve_fact(_deadlines(now), mode=ReadMode.PLANNING, now=now)
        ranks = [r.rank for r in result.candidates]
        assert ranks == sorted(ranks, reverse=True)


# ---------------------------------------------------------------- audit mode


class TestAuditMode:
    def test_returns_every_candidate(self) -> None:
        now = datetime.now(UTC)
        result = resolve_fact(_deadlines(now), mode=ReadMode.AUDIT, now=now)
        assert result.chosen is None
        assert len(result.candidates) == 3
        assert "audit mode" in result.reason

    def test_includes_non_active_candidates(self) -> None:
        """Audit must surface superseded / historical records too."""
        now = datetime.now(UTC)
        candidates = [
            _candidate("active", record_state=RecordState.ACTIVE, created_at=now),
            _candidate("superseded", record_state=RecordState.SUPERSEDED, created_at=now),
            _candidate("historical", record_state=RecordState.HISTORICAL, created_at=now),
        ]
        result = resolve_fact(candidates, mode=ReadMode.AUDIT, now=now)
        assert len(result.candidates) == 3
        states = {r.candidate.record_state for r in result.candidates}
        assert states == {RecordState.ACTIVE, RecordState.SUPERSEDED, RecordState.HISTORICAL}

    def test_still_detects_conflict_for_visibility(self) -> None:
        """Audit mode still computes the conflict flag so UIs can flag ties."""
        now = datetime.now(UTC)
        result = resolve_fact(_deadlines(now), mode=ReadMode.AUDIT, now=now)
        assert result.conflict_detected is True


# ---------------------------------------------------------------- sensitive mode


class TestSensitiveMode:
    def test_escalates_hitl_on_high_confidence_conflict(self) -> None:
        now = datetime.now(UTC)
        result = resolve_fact(
            _deadlines(now),
            mode=ReadMode.SENSITIVE,
            now=now,
            sensitive_min_confidence=0.7,
            sensitive_rank_delta=0.2,
        )
        assert result.requires_hitl is True
        assert result.chosen is None
        assert result.conflict_detected is True
        assert "escalating to human" in result.reason

    def test_picks_winner_when_no_conflict(self) -> None:
        """Single clear winner — sensitive mode should still pick it."""
        now = datetime.now(UTC)
        candidates = [
            _candidate("clear", confidence=0.95, author_trust=0.9, created_at=now),
            _candidate("far-behind", confidence=0.4, author_trust=0.4, created_at=now),
        ]
        result = resolve_fact(
            candidates,
            mode=ReadMode.SENSITIVE,
            now=now,
        )
        assert result.requires_hitl is False
        assert result.chosen is not None
        assert result.chosen.content == "clear"

    def test_agreement_between_authors_is_not_a_conflict(self) -> None:
        """Two authors agreeing on content must NOT trigger HITL."""
        now = datetime.now(UTC)
        candidates = [
            _candidate("same-content", confidence=0.9, author_did="did:key:z-a", created_at=now),
            _candidate("same-content", confidence=0.9, author_did="did:key:z-b", created_at=now),
        ]
        result = resolve_fact(candidates, mode=ReadMode.SENSITIVE, now=now)
        assert result.requires_hitl is False
        assert result.conflict_detected is False
        assert result.chosen is not None

    def test_low_confidence_candidate_does_not_raise_conflict(self) -> None:
        """Only high-confidence records participate in the conflict check."""
        now = datetime.now(UTC)
        candidates = [
            _candidate("strong", confidence=0.95, author_trust=0.9, created_at=now),
            _candidate("whisper", confidence=0.3, author_trust=0.8, created_at=now),
        ]
        result = resolve_fact(
            candidates,
            mode=ReadMode.SENSITIVE,
            now=now,
            sensitive_min_confidence=0.7,
        )
        assert result.requires_hitl is False

    def test_respects_rank_delta(self) -> None:
        """Rank delta controls how close two candidates must be to count as tied.

        Big delta = "even small rank differences count as ties" → more HITL.
        Small delta = "only near-identical ranks count as ties" → less HITL.
        """
        now = datetime.now(UTC)
        # Different content (so the agreement short-circuit doesn't kick in),
        # ranks are 0.45 vs 0.441 — a ~0.009 gap.
        candidates = [
            _candidate("A", confidence=0.9, author_trust=0.9, created_at=now),
            _candidate("B", confidence=0.9, author_trust=0.882, created_at=now),
        ]
        permissive = resolve_fact(
            candidates, mode=ReadMode.SENSITIVE, now=now, sensitive_rank_delta=0.05
        )
        strict = resolve_fact(
            candidates, mode=ReadMode.SENSITIVE, now=now, sensitive_rank_delta=0.001
        )
        # 0.009 gap: within 0.05 band → HITL; outside 0.001 band → no HITL.
        assert permissive.requires_hitl is True
        assert strict.requires_hitl is False

    def test_identical_content_never_triggers_conflict(self) -> None:
        """Two authors agreeing on content must never escalate, regardless of delta."""
        now = datetime.now(UTC)
        agreement = [
            _candidate("same", confidence=0.9, author_did="did:key:z-a", author_trust=0.9, created_at=now),
            _candidate("same", confidence=0.9, author_did="did:key:z-b", author_trust=0.9, created_at=now),
        ]
        for delta in (0.0001, 0.5, 1.0):
            result = resolve_fact(
                agreement, mode=ReadMode.SENSITIVE, now=now, sensitive_rank_delta=delta
            )
            assert result.requires_hitl is False, f"delta={delta} unexpectedly triggered HITL"
            assert result.chosen is not None


# ---------------------------------------------------------------- freshness


class TestFreshness:
    def test_older_records_lose_rank_over_time(self) -> None:
        now = datetime.now(UTC)
        candidates = [
            _candidate("recent", confidence=0.8, author_trust=0.8, created_at=now - timedelta(hours=1)),
            _candidate("ancient", confidence=0.8, author_trust=0.8, created_at=now - timedelta(days=365)),
        ]
        result = resolve_fact(candidates, mode=ReadMode.PLANNING, now=now)
        assert result.chosen is not None
        assert result.chosen.content == "recent"

    def test_freshness_factor_is_bounded(self) -> None:
        now = datetime.now(UTC)
        candidate = _candidate("x", created_at=now)
        assert 0 < candidate.freshness_factor(now=now) <= 1.0

    def test_future_timestamp_does_not_break(self) -> None:
        now = datetime.now(UTC)
        future_candidate = _candidate("from the future", created_at=now + timedelta(hours=1))
        assert future_candidate.freshness_factor(now=now) == 1.0


# ---------------------------------------------------------------- invariants


class TestInvariants:
    @pytest.mark.parametrize("mode", list(ReadMode))
    def test_always_returns_a_resolution(self, mode: ReadMode) -> None:
        now = datetime.now(UTC)
        result = resolve_fact(_deadlines(now), mode=mode, now=now)
        assert result.mode == mode
        assert result.reason  # non-empty justification

    def test_resolution_is_deterministic(self) -> None:
        """Given the exact same candidates, two runs produce identical outcomes."""
        now = datetime.now(UTC)
        candidates = _deadlines(now)
        a = resolve_fact(candidates, mode=ReadMode.PLANNING, now=now)
        b = resolve_fact(candidates, mode=ReadMode.PLANNING, now=now)
        assert a.chosen == b.chosen
        assert [r.rank for r in a.candidates] == [r.rank for r in b.candidates]
        assert [r.candidate.content for r in a.candidates] == [
            r.candidate.content for r in b.candidates
        ]

    def test_tie_break_is_stable(self) -> None:
        """Two candidates with exactly equal rank must order consistently."""
        now = datetime.now(UTC)
        c1 = _candidate("A", confidence=0.8, author_trust=0.8, created_at=now, content_hash="zA")
        c2 = _candidate("B", confidence=0.8, author_trust=0.8, created_at=now, content_hash="zB")
        r_a = resolve_fact([c1, c2], mode=ReadMode.AUDIT, now=now)
        r_b = resolve_fact([c2, c1], mode=ReadMode.AUDIT, now=now)
        # Stable across input order.
        assert [r.candidate.content for r in r_a.candidates] == [r.candidate.content for r in r_b.candidates]
