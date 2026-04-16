"""Bucketed Merkle fingerprint for CRDT reconciliation.

Two MassClaw nodes reconcile their memory state by exchanging a small
**summary** — not the full record set. The summary is a 256-entry table
indexed by the first byte of each record's content hash:

    bucket[i] = sha256(sorted_hashes_in_bucket_i)
    root      = sha256(bucket[0] || bucket[1] || ... || bucket[255])

Two nodes with identical state produce identical roots. Two nodes with
different state produce different roots; the index of the differing
bucket identifies which subset of records to reconcile.

This is a flat Merkle tree (one level of buckets), which is sufficient
for the scale we care about in Phase 1 (tens of thousands of records).
Deeper trees can be introduced later without changing the public
interface: :class:`MerkleSummary` would simply gain recursive children.

The reconciliation protocol itself (which runs over HTTP in Day 8+) is:

1. Peers exchange roots. If equal → done.
2. Peers exchange the 256 bucket digests. The differing bucket indices
   identify which subsets need reconciliation.
3. For each differing bucket, peers exchange the sorted list of hashes
   and pull any records the other has but they do not (via
   ``GET /api/v1/memory/by-hash/{hash}``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.crdt.hashing import HASH_MULTIBASE_PREFIX
from app.identity._base58 import b58decode

BUCKET_COUNT = 256  # one bucket per possible first byte of the hash


def bucket_for_hash(content_hash: str) -> int:
    """Return the bucket index (0..255) for a multibase-encoded hash.

    The hash's ``z`` prefix is stripped; the body is decoded from
    base58btc and its first byte selects the bucket. A malformed hash
    raises :class:`ValueError` — callers should filter those out before
    summarising, since a single bad hash would poison the root.
    """
    if not content_hash or not content_hash.startswith(HASH_MULTIBASE_PREFIX):
        raise ValueError(f"expected multibase '{HASH_MULTIBASE_PREFIX}'-prefixed hash, got {content_hash!r}")
    decoded = b58decode(content_hash[len(HASH_MULTIBASE_PREFIX) :])
    if not decoded:
        raise ValueError(f"hash body decodes to empty bytes: {content_hash!r}")
    return decoded[0]


@dataclass(frozen=True)
class MerkleSummary:
    """Fingerprint of a CRDT shared-memory state at one instant in time.

    - ``root`` — the top-level Merkle root (hex SHA-256).
    - ``buckets`` — one entry per bucket index 0..255, where each entry
      is either ``None`` (bucket empty) or a hex-encoded SHA-256 digest
      of the sorted hashes it contains.
    - ``record_count`` — total number of hashes summarised.

    Two summaries are equal when their ``root`` matches; an empty
    store has an all-``None`` buckets list and a well-defined root
    computed over 256 empty strings (so the comparison is still cheap).
    """

    root: str
    buckets: list[str | None] = field(default_factory=lambda: [None] * BUCKET_COUNT)
    record_count: int = 0

    def bucket(self, index: int) -> str | None:
        if not 0 <= index < BUCKET_COUNT:
            raise IndexError(f"bucket index out of range: {index}")
        return self.buckets[index]

    def diverges_from(self, other: MerkleSummary) -> list[int]:
        """Return the sorted list of bucket indices where two summaries differ.

        Empty list means the two nodes are fully reconciled.
        """
        if not isinstance(other, MerkleSummary):
            raise TypeError(f"expected MerkleSummary, got {type(other).__name__}")
        return [i for i in range(BUCKET_COUNT) if self.buckets[i] != other.buckets[i]]


def summarise_hashes(hashes: Iterable[str]) -> MerkleSummary:
    """Build a :class:`MerkleSummary` from an iterable of content hashes.

    The input need not be sorted or deduplicated; duplicates collapse
    naturally because a bucket's digest is taken over the sorted,
    unique hash strings within it.
    """
    buckets: list[set[str]] = [set() for _ in range(BUCKET_COUNT)]
    total = 0
    for h in hashes:
        try:
            idx = bucket_for_hash(h)
        except ValueError:
            # Skip malformed hashes so the summary stays well-defined.
            continue
        if h in buckets[idx]:
            continue
        buckets[idx].add(h)
        total += 1

    bucket_digests: list[str | None] = []
    for bucket in buckets:
        if not bucket:
            bucket_digests.append(None)
            continue
        body = "\n".join(sorted(bucket)).encode("utf-8")
        bucket_digests.append(hashlib.sha256(body).hexdigest())

    root_body = b"".join((d or "").encode("ascii") for d in bucket_digests)
    root = hashlib.sha256(root_body).hexdigest()
    return MerkleSummary(root=root, buckets=bucket_digests, record_count=total)
