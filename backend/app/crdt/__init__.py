"""CRDT shared memory — the federation substrate.

Three collaborating pieces:

- :mod:`app.crdt.hashing` — deterministic content hashing and the signable
  body definition. Every peer must agree on these or convergence is impossible.
- :mod:`app.crdt.merkle` — bucketed Merkle fingerprint used for fast
  reconciliation between nodes during gossip (Day 8+).
- :mod:`app.crdt.store` — the write path that wraps Ed25519 signing and
  hashing around :class:`MemoryRecord` inserts.

For Phase 1 the write contract is:

    CRDTStore(session).put(record_data, keypair=None) → MemoryRecord

``keypair`` may be ``None``, in which case the record is stored as a
legacy unsigned row (backward compatible). When a keypair is supplied,
the store computes the content hash, Ed25519-signs the canonical body,
and asserts the caller-supplied hash (if any) matches — guaranteeing
the record is federation-ready.
"""

from __future__ import annotations

from app.crdt.hashing import (
    HASH_MULTIBASE_PREFIX,
    CanonicalBody,
    build_canonical_body,
    compute_content_hash,
)
from app.crdt.merkle import (
    BUCKET_COUNT,
    MerkleSummary,
    bucket_for_hash,
    summarise_hashes,
)
from app.crdt.store import (
    CRDTStore,
    CRDTStoreError,
    SignatureMismatchError,
    UnknownAuthorError,
)

__all__ = [
    "BUCKET_COUNT",
    "CRDTStore",
    "CRDTStoreError",
    "CanonicalBody",
    "HASH_MULTIBASE_PREFIX",
    "MerkleSummary",
    "SignatureMismatchError",
    "UnknownAuthorError",
    "bucket_for_hash",
    "build_canonical_body",
    "compute_content_hash",
    "summarise_hashes",
]
