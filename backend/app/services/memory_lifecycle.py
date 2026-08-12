"""Memory record lifecycle policy — explicit, auditable, testable.

A CRDT memory record moves through four states:

    ACTIVE ──(superseded by trusted peer)──► SUPERSEDED
      │                                         │
      │                                         ▼
      ├────(policy archival)──────► HISTORICAL ─┘
      │                                         │
      ▼                                         ▼
    TOMBSTONED ◄─(explicit tombstone or expiry)┘

The rules live here as pure functions so they can be exhaustively tested
without a database, and so an operator reading the code can see every
transition in one file instead of hunting through the service.

Key invariants:

- A record is never silently deleted while the canonical content hash is
  still in the Merkle index — we either mark it ``TOMBSTONED`` (which the
  sync endpoints stop serving) and then GC later, or we leave it alone.
- Only the **original author** can tombstone their own record. Policy-
  driven archival (``active → historical``) does not require signature;
  it is an operator-level cleanup.
- Tombstones are themselves signed memory records, so a peer that sees a
  tombstone record can verify it was really authored by the right DID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.base import MemoryType, RecordState

# Default policy knobs — each is tunable at call time.
_DEFAULT_HISTORICAL_AFTER_HOURS = 90 * 24  # archive active records after 90 days.
_DEFAULT_TOMBSTONE_GRACE_HOURS = 24  # keep tombstoned records for 24h so peers can learn about them.
_DEFAULT_SUPERSEDE_TRUST_DELTA = 0.05  # the new author must be this much higher-trust to flip the parent.
_TOMBSTONE_METADATA_MARKER = "tombstone"


@dataclass(frozen=True)
class SupersedeDecision:
    """Result of asking 'does this new record supersede its parents?'."""

    supersede: bool
    reason: str
    target_hashes: tuple[str, ...] = field(default_factory=tuple)


def decide_supersede(
    *,
    new_author_trust: float | None,
    new_parent_hashes: list[str],
    parent_author_trusts: dict[str, float | None],
    trust_delta: float = _DEFAULT_SUPERSEDE_TRUST_DELTA,
) -> SupersedeDecision:
    """Decide whether the incoming record's parent hashes should flip to SUPERSEDED.

    Rule: the new record's author trust must exceed each parent's author
    trust by at least ``trust_delta``. Equal or lower trust means the new
    record is a "peer update" and both should remain ACTIVE. Unknown trust
    (``None``) is treated as the neutral default 0.5.
    """
    if not new_parent_hashes:
        return SupersedeDecision(supersede=False, reason="no parents cited")
    new_trust = 0.5 if new_author_trust is None else float(new_author_trust)
    target: list[str] = []
    for parent_hash in new_parent_hashes:
        parent_trust_raw = parent_author_trusts.get(parent_hash)
        parent_trust = 0.5 if parent_trust_raw is None else float(parent_trust_raw)
        if new_trust >= parent_trust + trust_delta:
            target.append(parent_hash)
    if not target:
        return SupersedeDecision(
            supersede=False,
            reason=f"new author trust {new_trust:.2f} does not exceed parents' by {trust_delta:.2f}",
        )
    return SupersedeDecision(
        supersede=True,
        reason=f"new author trust {new_trust:.2f} exceeds {len(target)} parent(s) by ≥{trust_delta:.2f}",
        target_hashes=tuple(target),
    )


@dataclass(frozen=True)
class ArchiveCandidate:
    """A record eligible for active → historical archival by policy."""

    memory_id: str
    content_hash: str | None
    age_hours: float


def is_archive_candidate(
    *,
    record_state: RecordState,
    created_at: datetime,
    now: datetime | None = None,
    archive_after_hours: int = _DEFAULT_HISTORICAL_AFTER_HOURS,
) -> bool:
    """Return True when ``record_state`` + age policy says it should be archived."""
    if record_state not in (RecordState.ACTIVE, RecordState.SUPERSEDED):
        return False
    reference = now or datetime.now(UTC)
    created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    age_hours = (reference - created).total_seconds() / 3600.0
    return age_hours >= archive_after_hours


def is_tombstone_gc_eligible(
    *,
    record_state: RecordState,
    created_at: datetime,
    now: datetime | None = None,
    grace_hours: int = _DEFAULT_TOMBSTONE_GRACE_HOURS,
) -> bool:
    """Return True when a tombstoned record has been around long enough to hard-delete."""
    if record_state != RecordState.TOMBSTONED:
        return False
    reference = now or datetime.now(UTC)
    created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    age_hours = (reference - created).total_seconds() / 3600.0
    return age_hours >= grace_hours


# ------------------------------------------------------------------ Tombstone authorship


class TombstoneAuthError(ValueError):
    """Raised when a tombstone attempt violates the authorship contract."""


def assert_can_tombstone(
    *,
    requesting_did: str | None,
    target_author_did: str | None,
    target_state: RecordState,
) -> None:
    """Verify the caller is allowed to tombstone the target record.

    Phase 1 rule: only the record's original author can tombstone it. A
    future policy engine extension may add delegated revokers (e.g. an
    admin DID), but we keep the v1 contract simple so authorship is the
    single source of trust.
    """
    if target_state == RecordState.TOMBSTONED:
        raise TombstoneAuthError("record is already tombstoned")
    if target_author_did is None:
        raise TombstoneAuthError(
            "target record has no author_did — unsigned legacy records cannot be tombstoned; "
            "use the admin GC worker instead"
        )
    if requesting_did is None:
        raise TombstoneAuthError("tombstone request must carry a DID in its author_did field")
    if requesting_did != target_author_did:
        raise TombstoneAuthError(
            f"only the original author ({target_author_did!r}) may tombstone this record; "
            f"received request from {requesting_did!r}"
        )


# ------------------------------------------------------------------ Tombstone record shape


def build_tombstone_metadata(
    *,
    target_hash: str,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical metadata shape for a tombstone record.

    Keeping the marker in ``metadata[tombstone]`` = True rather than
    inventing a new ``memory_type`` lets every query / indexing path keep
    working unchanged; consumers that care about tombstones read the
    metadata marker.
    """
    meta: dict[str, Any] = dict(extra or {})
    meta[_TOMBSTONE_METADATA_MARKER] = True
    meta["tombstone_target"] = target_hash
    if reason:
        meta["tombstone_reason"] = reason
    return meta


def is_tombstone_record(metadata: dict[str, Any] | None) -> bool:
    """Return True when ``metadata`` marks a record as a tombstone."""
    return bool(metadata and metadata.get(_TOMBSTONE_METADATA_MARKER) is True)


def tombstone_target_hash(metadata: dict[str, Any] | None) -> str | None:
    """Extract the tombstone's target hash, or ``None`` if not a tombstone."""
    if not is_tombstone_record(metadata):
        return None
    target = (metadata or {}).get("tombstone_target")
    return target if isinstance(target, str) and target else None


def tombstone_memory_type() -> MemoryType:
    """The canonical memory_type for tombstone records.

    Reuses the existing ``META`` enum member rather than introducing a new
    one — keeps the Postgres enum type stable and avoids a migration for
    a purely logical distinction.
    """
    return MemoryType.META


# ------------------------------------------------------------------ GC policy summary


@dataclass(frozen=True)
class GCRunPolicy:
    """Operator-facing switches for :meth:`MemoryService.garbage_collect`.

    Kept as a dataclass so new knobs can be added without changing the
    public signature every time.
    """

    tombstone_grace_hours: int = _DEFAULT_TOMBSTONE_GRACE_HOURS
    archive_active_after_hours: int = _DEFAULT_HISTORICAL_AFTER_HOURS
    low_confidence_threshold: float = 0.01
    hard_delete_max_age_hours: int | None = None
    #: Ceiling on live semantic cache entries; the oldest beyond it are
    #: trimmed. ``None`` means "use settings.semantic_cache_max_entries",
    #: which is the normal path — the field exists so a caller (or a test)
    #: can pin the cap without touching global settings.
    cache_max_entries: int | None = None

    def archive_cutoff(self, now: datetime | None = None) -> datetime:
        reference = now or datetime.now(UTC)
        return reference - timedelta(hours=self.archive_active_after_hours)

    def tombstone_cutoff(self, now: datetime | None = None) -> datetime:
        reference = now or datetime.now(UTC)
        return reference - timedelta(hours=self.tombstone_grace_hours)
