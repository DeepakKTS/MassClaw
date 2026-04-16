"""Three-mode fact resolution for CRDT shared memory.

The CRDT store converges on a **set** of signed records; it does NOT pick a
winner when two or more records disagree on a given semantic fact (e.g.
"what is the deadline?"). The read-time resolver in this module turns that
set into a single actionable outcome based on the caller's tolerance:

- **Planning** — pick the top-ranked candidate and move on. Ranking is
  ``confidence × freshness_factor × author_trust``.
- **Audit** — return every candidate with its rank so the dashboard, the
  policy engine, or a human auditor can see the whole picture.
- **Sensitive** — if two candidates agree within a small confidence band,
  refuse to pick; require a human to approve. Returns ``requires_hitl=True``
  with the full candidate list so the HITL UI can show them side by side.

A "high-confidence conflict" is two or more candidates whose confidence is
each above ``sensitive_conflict_min_confidence`` (default 0.7) and whose
rank scores are within ``sensitive_conflict_rank_delta`` of each other
(default 0.15) — in that regime we cannot statistically distinguish them
and must not pretend otherwise.

This module is pure Python: no database, no HTTP, no LLM. It operates on
:class:`FactCandidate` values the caller prepares by translating
:class:`MemoryRecord` rows (plus optional agent trust scores) into
:class:`FactCandidate` tuples. That separation lets us test the resolver
exhaustively without spinning up Postgres.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.base import ReadMode, RecordState

# Defaults chosen so most workflows behave sensibly; all tunable at call time.
_DEFAULT_FRESHNESS_LAMBDA = 0.01  # per-hour decay constant for freshness.
_DEFAULT_AUTHOR_TRUST = 0.5  # when author trust is unknown, treat as neutral.
_DEFAULT_SENSITIVE_MIN_CONFIDENCE = 0.7
_DEFAULT_SENSITIVE_RANK_DELTA = 0.15


@dataclass(frozen=True)
class FactCandidate:
    """One memory record being considered as the answer to a fact query."""

    memory_id: uuid.UUID
    content: str
    confidence: float
    author_did: str | None
    author_trust: float | None  # None when unknown; resolver uses the neutral default.
    record_state: RecordState
    content_hash: str | None
    created_at: datetime
    similarity: float  # vector-search similarity against the subject query.

    def freshness_factor(
        self,
        *,
        now: datetime | None = None,
        decay_lambda: float = _DEFAULT_FRESHNESS_LAMBDA,
    ) -> float:
        """Return ``exp(-lambda * hours_since_created)``; bounded to (0, 1]."""
        reference = now or datetime.now(UTC)
        created = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=UTC)
        hours_since = max(0.0, (reference - created).total_seconds() / 3600.0)
        return math.exp(-decay_lambda * hours_since)

    def rank(
        self,
        *,
        now: datetime | None = None,
        decay_lambda: float = _DEFAULT_FRESHNESS_LAMBDA,
        default_author_trust: float = _DEFAULT_AUTHOR_TRUST,
    ) -> float:
        """Return the scalar used to order candidates in planning mode."""
        trust = self.author_trust if self.author_trust is not None else default_author_trust
        return float(self.confidence) * float(self.freshness_factor(now=now, decay_lambda=decay_lambda)) * float(trust)


@dataclass(frozen=True)
class RankedCandidate:
    """A :class:`FactCandidate` plus its computed rank, for audit output."""

    candidate: FactCandidate
    rank: float
    freshness_factor: float
    effective_author_trust: float


@dataclass(frozen=True)
class FactResolution:
    """Outcome of a fact resolution.

    - ``mode`` — the read mode that produced this result.
    - ``chosen`` — the winning candidate in planning mode, or ``None`` in
      audit / sensitive-with-conflict.
    - ``candidates`` — every candidate considered, ranked desc. Always
      populated, even for planning mode.
    - ``requires_hitl`` — true only for sensitive mode, when a
      high-confidence conflict was detected.
    - ``conflict_detected`` — true when two candidates are within the
      sensitive rank-delta band (independent of mode). Always computed so
      callers in other modes can react if they choose.
    - ``reason`` — a short human-readable justification of the outcome.
    """

    mode: ReadMode
    chosen: FactCandidate | None
    candidates: list[RankedCandidate] = field(default_factory=list)
    requires_hitl: bool = False
    conflict_detected: bool = False
    reason: str = ""


def resolve_fact(
    candidates: list[FactCandidate],
    *,
    mode: ReadMode,
    now: datetime | None = None,
    decay_lambda: float = _DEFAULT_FRESHNESS_LAMBDA,
    default_author_trust: float = _DEFAULT_AUTHOR_TRUST,
    sensitive_min_confidence: float = _DEFAULT_SENSITIVE_MIN_CONFIDENCE,
    sensitive_rank_delta: float = _DEFAULT_SENSITIVE_RANK_DELTA,
) -> FactResolution:
    """Resolve a set of CRDT candidates to a single outcome per ``mode``.

    Only ``RecordState.ACTIVE`` candidates are eligible for ``planning``
    and ``sensitive`` modes. ``audit`` mode returns every candidate the
    caller handed in (the caller controls lifecycle filtering by choosing
    what to pass).
    """
    active = [c for c in candidates if c.record_state == RecordState.ACTIVE]

    ranked = _rank_candidates(
        active,
        now=now,
        decay_lambda=decay_lambda,
        default_author_trust=default_author_trust,
    )
    # Audit uses everything the caller provided, not just active.
    audit_ranked = (
        ranked
        if mode != ReadMode.AUDIT
        else _rank_candidates(
            candidates,
            now=now,
            decay_lambda=decay_lambda,
            default_author_trust=default_author_trust,
        )
    )

    conflict = _has_high_confidence_conflict(
        ranked,
        min_confidence=sensitive_min_confidence,
        rank_delta=sensitive_rank_delta,
    )

    if mode == ReadMode.AUDIT:
        return FactResolution(
            mode=mode,
            chosen=None,
            candidates=audit_ranked,
            conflict_detected=conflict,
            reason=f"audit mode — returning {len(audit_ranked)} candidates",
        )

    if not ranked:
        return FactResolution(
            mode=mode,
            chosen=None,
            candidates=[],
            conflict_detected=False,
            reason="no active candidates matched the query",
        )

    if mode == ReadMode.SENSITIVE and conflict:
        return FactResolution(
            mode=mode,
            chosen=None,
            candidates=ranked,
            requires_hitl=True,
            conflict_detected=True,
            reason=(f"high-confidence conflict among {len(ranked)} candidates; escalating to human approval"),
        )

    # Planning mode — or sensitive mode without conflict.
    winner = ranked[0]
    return FactResolution(
        mode=mode,
        chosen=winner.candidate,
        candidates=ranked,
        conflict_detected=conflict,
        reason=(f"selected top-ranked candidate (rank={winner.rank:.4f}) from {len(ranked)} active records"),
    )


# ------------------------------------------------------------------ Internals


def _rank_candidates(
    candidates: list[FactCandidate],
    *,
    now: datetime | None,
    decay_lambda: float,
    default_author_trust: float,
) -> list[RankedCandidate]:
    """Return candidates sorted by rank descending, with tie-breakers."""
    scored: list[RankedCandidate] = []
    for c in candidates:
        trust = c.author_trust if c.author_trust is not None else default_author_trust
        freshness = c.freshness_factor(now=now, decay_lambda=decay_lambda)
        rank = float(c.confidence) * float(freshness) * float(trust)
        scored.append(
            RankedCandidate(
                candidate=c,
                rank=round(rank, 6),
                freshness_factor=round(freshness, 6),
                effective_author_trust=round(float(trust), 6),
            )
        )
    # Sort by rank desc, then by created_at desc, then by content_hash for stability.
    scored.sort(
        key=lambda r: (
            -r.rank,
            -(r.candidate.created_at.timestamp()),
            r.candidate.content_hash or "",
        )
    )
    return scored


def _has_high_confidence_conflict(
    ranked: list[RankedCandidate],
    *,
    min_confidence: float,
    rank_delta: float,
) -> bool:
    """Detect whether at least two ranked candidates are statistically tied.

    The tie band is: both candidates above ``min_confidence`` AND their
    ranks within ``rank_delta`` of each other AND their content differs.
    Identical content from different authors is NOT a conflict — it's
    agreement across the federation and should be celebrated.
    """
    if len(ranked) < 2:
        return False
    top = ranked[0]
    if top.candidate.confidence < min_confidence:
        return False
    for other in ranked[1:]:
        if other.candidate.confidence < min_confidence:
            continue
        if other.candidate.content == top.candidate.content:
            continue
        if abs(top.rank - other.rank) <= rank_delta:
            return True
    return False
