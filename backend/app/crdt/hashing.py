"""Canonical hashing for memory records in the CRDT shared store.

Every peer on the network must agree on:

1. Which fields constitute the **signable body** of a record (immutable
   content + provenance + authorship; nothing server-generated).
2. How those fields are serialised into deterministic bytes.
3. How those bytes are hashed into a stable, multibase-encoded address.

A disagreement on any of the above is an instant federation fault —
two peers would produce different hashes for the same record, which
ripples through the Merkle index and shared memory reads. These three
definitions are therefore held here, in one place, with stable names.

Design choices:

- Hash: **SHA-256** over the canonical JSON bytes. Chosen because every
  language has it and the 32-byte digest is small enough to embed in URLs.
- Encoding: **multibase base58btc** (``z`` prefix) to match the signer /
  DID encoding used elsewhere in MassClaw. A hash looks like
  ``z6Mk...`` — visually similar to the did:key identifier, which helps
  reviewers recognise signed-record addresses quickly.
- Canonicalisation: same ``canonicalize()`` primitive used for signing
  AgentFacts documents (RFC 8785-style: sorted keys, no whitespace,
  minimal escapes, finite floats).

The canonical body deliberately excludes server-chosen fields
(``memory_id``, ``version``, ``created_at``, ``expires_at``, ``freshness``,
``record_state``, ``embedding``) and CRDT fields that depend on the hash
itself (``content_hash``, ``signature``). Peers compute the same hash for
the same semantic write regardless of when/where it lands.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from app.identity.canonicalize import canonicalize

HASH_MULTIBASE_PREFIX = "z"


@dataclass(frozen=True)
class CanonicalBody:
    """The immutable, signable subset of a memory record.

    Two records with equal :class:`CanonicalBody` values MUST produce
    the same content hash and therefore the same federation identity.
    Equality is structural, not object-identity-based.
    """

    workflow_id: str
    memory_type: str
    content: str
    confidence: float
    metadata: dict[str, Any]
    parent_hashes: tuple[str, ...]
    author_did: str | None
    source_agent_id: str | None

    def as_jsonable(self) -> dict[str, Any]:
        """Return the JSON-serialisable dict this body will be canonicalised from."""
        return {
            "author_did": self.author_did,
            "confidence": float(self.confidence),
            "content": self.content,
            "memory_type": str(self.memory_type),
            "metadata": _clone_jsonable(self.metadata),
            "parent_hashes": sorted(self.parent_hashes),
            "source_agent_id": self.source_agent_id,
            "workflow_id": self.workflow_id,
        }

    def to_signable_bytes(self) -> bytes:
        """Return the RFC 8785-canonical byte representation of this body."""
        return canonicalize(self.as_jsonable())


def build_canonical_body(
    *,
    workflow_id: uuid.UUID | str,
    memory_type: str,
    content: str,
    confidence: float,
    metadata: dict[str, Any] | None = None,
    parent_hashes: list[str] | tuple[str, ...] | None = None,
    author_did: str | None = None,
    source_agent_id: uuid.UUID | str | None = None,
) -> CanonicalBody:
    """Construct a :class:`CanonicalBody` from a write request / ORM row."""
    return CanonicalBody(
        workflow_id=str(workflow_id),
        memory_type=str(memory_type),
        content=content,
        confidence=float(confidence),
        metadata=dict(metadata or {}),
        parent_hashes=tuple(parent_hashes or ()),
        author_did=author_did,
        source_agent_id=str(source_agent_id) if source_agent_id is not None else None,
    )


def compute_content_hash(body: CanonicalBody) -> str:
    """Return the multibase-encoded SHA-256 of ``body``.

    The return value is a single string beginning with ``z`` (multibase
    base58btc). For a given :class:`CanonicalBody` the result is stable
    across processes, platforms, and Python runs.
    """
    digest = hashlib.sha256(body.to_signable_bytes()).digest()
    return HASH_MULTIBASE_PREFIX + _base58btc_encode(digest)


def _base58btc_encode(raw: bytes) -> str:
    """Encode bytes in base58btc without the multibase prefix.

    Delegates to the same encoder used by identity keys so agreement is
    guaranteed.
    """
    from app.identity._base58 import b58encode

    return b58encode(raw)


def _clone_jsonable(value: Any) -> Any:
    """Deep-copy-ish clone that keeps the value JSON-safe.

    Tuples become lists, nested dicts are recursively cloned. Scalars
    pass through unchanged. This guarantees the canonicaliser never
    sees a Python object it would reject.
    """
    if isinstance(value, dict):
        return {str(k): _clone_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
