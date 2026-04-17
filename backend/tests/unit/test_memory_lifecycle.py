"""Unit tests for the pure memory lifecycle policy module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.base import MemoryType, RecordState
from app.services.memory_lifecycle import (
    GCRunPolicy,
    TombstoneAuthError,
    assert_can_tombstone,
    build_tombstone_metadata,
    decide_supersede,
    is_archive_candidate,
    is_tombstone_gc_eligible,
    is_tombstone_record,
    tombstone_memory_type,
    tombstone_target_hash,
)


# ---------------------------------------------------------------- decide_supersede


class TestDecideSupersede:
    def test_no_parents_returns_false(self) -> None:
        result = decide_supersede(new_author_trust=0.9, new_parent_hashes=[], parent_author_trusts={})
        assert result.supersede is False
        assert "no parents" in result.reason

    def test_higher_trust_flips_parents(self) -> None:
        result = decide_supersede(
            new_author_trust=0.9,
            new_parent_hashes=["zparent1", "zparent2"],
            parent_author_trusts={"zparent1": 0.5, "zparent2": 0.6},
        )
        assert result.supersede is True
        assert set(result.target_hashes) == {"zparent1", "zparent2"}

    def test_only_parents_meeting_delta_are_flipped(self) -> None:
        result = decide_supersede(
            new_author_trust=0.7,
            new_parent_hashes=["low", "close"],
            parent_author_trusts={"low": 0.3, "close": 0.68},
            trust_delta=0.05,
        )
        assert result.supersede is True
        assert result.target_hashes == ("low",)

    def test_equal_trust_does_not_flip(self) -> None:
        result = decide_supersede(
            new_author_trust=0.7,
            new_parent_hashes=["peer"],
            parent_author_trusts={"peer": 0.7},
        )
        assert result.supersede is False

    def test_unknown_parent_trust_defaults_to_neutral(self) -> None:
        result = decide_supersede(
            new_author_trust=0.8,
            new_parent_hashes=["unknown"],
            parent_author_trusts={"unknown": None},
        )
        # Default parent trust = 0.5; 0.8 > 0.5 + 0.05 → flip.
        assert result.supersede is True

    def test_unknown_new_author_defaults_to_neutral(self) -> None:
        result = decide_supersede(
            new_author_trust=None,
            new_parent_hashes=["strong"],
            parent_author_trusts={"strong": 0.9},
        )
        assert result.supersede is False


# ---------------------------------------------------------------- archival


class TestArchiveCandidate:
    def test_active_old_record_qualifies(self) -> None:
        now = datetime.now(UTC)
        assert is_archive_candidate(
            record_state=RecordState.ACTIVE,
            created_at=now - timedelta(hours=100 * 24),
            now=now,
            archive_after_hours=90 * 24,
        )

    def test_young_record_does_not_qualify(self) -> None:
        now = datetime.now(UTC)
        assert not is_archive_candidate(
            record_state=RecordState.ACTIVE,
            created_at=now - timedelta(hours=1),
            now=now,
            archive_after_hours=90 * 24,
        )

    def test_tombstoned_never_archives(self) -> None:
        now = datetime.now(UTC)
        assert not is_archive_candidate(
            record_state=RecordState.TOMBSTONED,
            created_at=now - timedelta(days=365),
            now=now,
        )

    def test_historical_does_not_archive_again(self) -> None:
        now = datetime.now(UTC)
        assert not is_archive_candidate(
            record_state=RecordState.HISTORICAL,
            created_at=now - timedelta(days=365),
            now=now,
        )

    def test_naive_datetime_handled(self) -> None:
        """Timezone-naive timestamps are coerced to UTC."""
        now = datetime.now(UTC)
        naive_created = (now - timedelta(days=200)).replace(tzinfo=None)
        assert is_archive_candidate(
            record_state=RecordState.ACTIVE,
            created_at=naive_created,
            now=now,
        )


# ---------------------------------------------------------------- GC eligibility


class TestTombstoneGcEligible:
    def test_recent_tombstone_protected(self) -> None:
        now = datetime.now(UTC)
        assert not is_tombstone_gc_eligible(
            record_state=RecordState.TOMBSTONED,
            created_at=now - timedelta(hours=1),
            now=now,
            grace_hours=24,
        )

    def test_old_tombstone_eligible(self) -> None:
        now = datetime.now(UTC)
        assert is_tombstone_gc_eligible(
            record_state=RecordState.TOMBSTONED,
            created_at=now - timedelta(hours=48),
            now=now,
            grace_hours=24,
        )

    def test_non_tombstoned_never_eligible(self) -> None:
        now = datetime.now(UTC)
        for state in (RecordState.ACTIVE, RecordState.SUPERSEDED, RecordState.HISTORICAL):
            assert not is_tombstone_gc_eligible(
                record_state=state,
                created_at=now - timedelta(days=365),
                now=now,
            )


# ---------------------------------------------------------------- tombstone authorship


class TestAssertCanTombstone:
    def test_author_can_tombstone_own_record(self) -> None:
        assert_can_tombstone(
            requesting_did="did:key:z-alpha",
            target_author_did="did:key:z-alpha",
            target_state=RecordState.ACTIVE,
        )

    def test_non_author_rejected(self) -> None:
        with pytest.raises(TombstoneAuthError) as exc:
            assert_can_tombstone(
                requesting_did="did:key:z-other",
                target_author_did="did:key:z-alpha",
                target_state=RecordState.ACTIVE,
            )
        assert "only the original author" in str(exc.value)

    def test_already_tombstoned_rejected(self) -> None:
        with pytest.raises(TombstoneAuthError) as exc:
            assert_can_tombstone(
                requesting_did="did:key:z-alpha",
                target_author_did="did:key:z-alpha",
                target_state=RecordState.TOMBSTONED,
            )
        assert "already tombstoned" in str(exc.value)

    def test_unsigned_target_rejected(self) -> None:
        with pytest.raises(TombstoneAuthError) as exc:
            assert_can_tombstone(
                requesting_did="did:key:z-alpha",
                target_author_did=None,
                target_state=RecordState.ACTIVE,
            )
        assert "unsigned legacy records" in str(exc.value)

    def test_missing_requesting_did_rejected(self) -> None:
        with pytest.raises(TombstoneAuthError):
            assert_can_tombstone(
                requesting_did=None,
                target_author_did="did:key:z-alpha",
                target_state=RecordState.ACTIVE,
            )


# ---------------------------------------------------------------- tombstone shape


class TestTombstoneShape:
    def test_metadata_contains_marker_and_target(self) -> None:
        meta = build_tombstone_metadata(target_hash="zabcd", reason="merged into zlater")
        assert meta["tombstone"] is True
        assert meta["tombstone_target"] == "zabcd"
        assert meta["tombstone_reason"] == "merged into zlater"

    def test_metadata_carries_extra_fields(self) -> None:
        meta = build_tombstone_metadata(target_hash="zabcd", extra={"requested_by": "ops-team"})
        assert meta["requested_by"] == "ops-team"
        assert meta["tombstone"] is True

    def test_is_tombstone_record_detects_marker(self) -> None:
        assert is_tombstone_record({"tombstone": True, "tombstone_target": "zx"})
        assert not is_tombstone_record({"tombstone": False})
        assert not is_tombstone_record({})
        assert not is_tombstone_record(None)

    def test_tombstone_target_hash(self) -> None:
        meta = build_tombstone_metadata(target_hash="zabc")
        assert tombstone_target_hash(meta) == "zabc"
        assert tombstone_target_hash({"tombstone": False}) is None
        assert tombstone_target_hash(None) is None

    def test_memory_type_is_meta(self) -> None:
        assert tombstone_memory_type() is MemoryType.META


# ---------------------------------------------------------------- GC policy


class TestGCRunPolicy:
    def test_default_values(self) -> None:
        policy = GCRunPolicy()
        assert policy.tombstone_grace_hours == 24
        assert policy.archive_active_after_hours == 90 * 24
        assert policy.low_confidence_threshold == 0.01
        assert policy.hard_delete_max_age_hours is None

    def test_archive_cutoff_is_in_the_past(self) -> None:
        now = datetime.now(UTC)
        policy = GCRunPolicy(archive_active_after_hours=48)
        assert policy.archive_cutoff(now=now) == now - timedelta(hours=48)

    def test_tombstone_cutoff_is_in_the_past(self) -> None:
        now = datetime.now(UTC)
        policy = GCRunPolicy(tombstone_grace_hours=2)
        assert policy.tombstone_cutoff(now=now) == now - timedelta(hours=2)
