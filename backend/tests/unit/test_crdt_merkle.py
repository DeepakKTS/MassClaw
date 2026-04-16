"""Unit tests for the bucketed Merkle fingerprint used in gossip reconciliation."""

from __future__ import annotations

import pytest

from app.crdt.hashing import HASH_MULTIBASE_PREFIX
from app.crdt.merkle import (
    BUCKET_COUNT,
    MerkleSummary,
    bucket_for_hash,
    summarise_hashes,
)
from app.identity._base58 import b58encode


def _hash_with_first_byte(b: int, tail: bytes = b"\x00" * 31) -> str:
    assert 0 <= b < 256
    return HASH_MULTIBASE_PREFIX + b58encode(bytes([b]) + tail)


# ---------------------------------------------------------------- bucket_for_hash


class TestBucketIndex:
    def test_bucket_matches_first_byte(self) -> None:
        for target in (0, 1, 42, 127, 200, 255):
            h = _hash_with_first_byte(target)
            assert bucket_for_hash(h) == target

    def test_malformed_hash_raises(self) -> None:
        with pytest.raises(ValueError):
            bucket_for_hash("not-multibase")
        with pytest.raises(ValueError):
            bucket_for_hash("f01")  # wrong prefix

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            bucket_for_hash("")

    def test_empty_body_after_prefix_raises(self) -> None:
        with pytest.raises(ValueError):
            bucket_for_hash(HASH_MULTIBASE_PREFIX)


# ---------------------------------------------------------------- summarise_hashes


class TestSummariseHashes:
    def test_empty_input_produces_stable_root(self) -> None:
        s1 = summarise_hashes([])
        s2 = summarise_hashes([])
        assert s1.root == s2.root
        assert s1.record_count == 0
        assert all(b is None for b in s1.buckets)
        assert len(s1.buckets) == BUCKET_COUNT

    def test_single_hash_counts_as_one(self) -> None:
        h = _hash_with_first_byte(0x10)
        summary = summarise_hashes([h])
        assert summary.record_count == 1
        assert summary.buckets[0x10] is not None
        # All other buckets are empty.
        for i in range(BUCKET_COUNT):
            if i != 0x10:
                assert summary.buckets[i] is None

    def test_input_order_does_not_matter(self) -> None:
        hashes = [_hash_with_first_byte(i, bytes([i]) * 31) for i in range(10)]
        s1 = summarise_hashes(hashes)
        s2 = summarise_hashes(list(reversed(hashes)))
        assert s1.root == s2.root
        assert s1.buckets == s2.buckets

    def test_duplicates_collapse(self) -> None:
        h = _hash_with_first_byte(0x33)
        s_unique = summarise_hashes([h])
        s_dup = summarise_hashes([h, h, h])
        assert s_unique.root == s_dup.root
        assert s_dup.record_count == 1

    def test_malformed_hashes_skipped(self) -> None:
        good = _hash_with_first_byte(0x55)
        summary = summarise_hashes([good, "not-multibase", "", "f01"])
        assert summary.record_count == 1

    def test_different_sets_produce_different_roots(self) -> None:
        a = _hash_with_first_byte(0x10, b"\x01" * 31)
        b = _hash_with_first_byte(0x10, b"\x02" * 31)
        assert summarise_hashes([a]).root != summarise_hashes([b]).root

    def test_diverges_from_identifies_different_buckets(self) -> None:
        a = _hash_with_first_byte(0x10)
        b = _hash_with_first_byte(0x20)
        s_a = summarise_hashes([a])
        s_b = summarise_hashes([b])
        diverging = s_a.diverges_from(s_b)
        assert 0x10 in diverging
        assert 0x20 in diverging
        assert 0x30 not in diverging

    def test_diverges_from_empty_when_equal(self) -> None:
        a = _hash_with_first_byte(0x10)
        s = summarise_hashes([a])
        assert s.diverges_from(s) == []

    def test_diverges_from_rejects_wrong_type(self) -> None:
        s = summarise_hashes([])
        with pytest.raises(TypeError):
            s.diverges_from("not a summary")  # type: ignore[arg-type]


# ---------------------------------------------------------------- Summary API


class TestMerkleSummary:
    def test_bucket_access_by_index(self) -> None:
        summary = summarise_hashes([_hash_with_first_byte(0x42)])
        assert summary.bucket(0x42) is not None
        assert summary.bucket(0x43) is None

    def test_bucket_out_of_range(self) -> None:
        summary = summarise_hashes([])
        with pytest.raises(IndexError):
            summary.bucket(256)
        with pytest.raises(IndexError):
            summary.bucket(-1)

    def test_default_buckets_fill_to_256(self) -> None:
        empty = MerkleSummary(root="x")
        assert len(empty.buckets) == BUCKET_COUNT
