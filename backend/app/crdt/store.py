"""CRDT write path — append-only, signed, content-addressed memory writes.

:class:`CRDTStore` wraps the database insert of a :class:`MemoryRecord` so
that three things happen together, in one transaction:

1. The canonical body is assembled (see :mod:`app.crdt.hashing`).
2. A content hash is computed; if the caller supplied one, the two must agree.
3. An Ed25519 signature is computed (or verified, when caller-supplied).

The store supports two operational modes:

- **Legacy / unsigned**: no ``keypair`` is provided. The record is written
  as a backward-compatible row with ``author_did = None``, no signature,
  and no content hash. This keeps older callers (and tests that predate
  federation) working unchanged.
- **Signed / federation-ready**: a ``KeyPair`` is supplied. The record is
  signed, its hash is computed, and both are persisted. Signed records
  are what peers gossip about; unsigned records never leave the node.

When a caller pre-computes the hash and signature (typical for federation
writes inbound from a peer), the store re-derives both and **rejects the
write** if either disagrees. This is the contract that prevents a
malicious peer from seeding us with records that claim a valid hash but
were not actually signed by the claimed author.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crdt.hashing import (
    CanonicalBody,
    build_canonical_body,
    compute_content_hash,
)
from app.identity.did import decode_did_key, decode_did_nanda, parse_did
from app.identity.signer import (
    KeyPair,
    decode_multibase,
    encode_multibase,
    sign_bytes,
    verify_bytes,
)
from app.models.base import MemoryType, RecordState
from app.models.memory import MemoryRecord


class CRDTStoreError(Exception):
    """Base class for CRDT write-path errors."""


class SignatureMismatchError(CRDTStoreError):
    """Raised when a caller-supplied hash or signature does not verify."""


class UnknownAuthorError(CRDTStoreError):
    """Raised when ``author_did`` cannot be resolved to a public key."""


class CRDTStore:
    """Append-only, signed, content-addressed store for memory records.

    The store does not cache, batch, or debounce — it is a thin wrapper
    around a single ``session.add(record)`` with deterministic hashing and
    Ed25519 signing on either side. Higher-level batching / gossip lives
    above it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ Public

    async def put(
        self,
        *,
        workflow_id: uuid.UUID,
        memory_type: MemoryType,
        content: str,
        confidence: float,
        metadata: dict | None = None,
        parent_hashes: list[str] | None = None,
        author_did: str | None = None,
        source_agent_id: uuid.UUID | None = None,
        keypair: KeyPair | None = None,
        precomputed_hash: str | None = None,
        precomputed_signature: str | None = None,
        embedding: list[float] | None = None,
        version: int = 1,
        parent_version_id: uuid.UUID | None = None,
        expires_at=None,
        record_state: RecordState = RecordState.ACTIVE,
    ) -> MemoryRecord:
        """Write a memory record, signed + content-addressed where possible.

        Returns the persisted :class:`MemoryRecord`. Raises:

        - :class:`SignatureMismatchError` — caller-supplied hash/signature
          does not match the canonical body.
        - :class:`UnknownAuthorError` — ``author_did`` is not a DID method
          whose public key we can derive (did:key / did:nanda).
        - :class:`CRDTStoreError` — any other write failure, most commonly
          a duplicate content hash (federation writes that land twice).
        """
        body = build_canonical_body(
            workflow_id=workflow_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            metadata=metadata,
            parent_hashes=parent_hashes,
            author_did=author_did,
            source_agent_id=source_agent_id,
        )

        content_hash, signature = self._resolve_hash_and_signature(
            body=body,
            keypair=keypair,
            precomputed_hash=precomputed_hash,
            precomputed_signature=precomputed_signature,
        )

        record = MemoryRecord(
            workflow_id=workflow_id,
            source_agent_id=source_agent_id,
            memory_type=memory_type,
            content=content,
            embedding=embedding,
            metadata_=dict(metadata or {}),
            confidence=float(confidence),
            freshness=1.0,
            version=version,
            parent_version_id=parent_version_id,
            author_did=author_did,
            parent_hashes=list(body.parent_hashes),
            signature=signature,
            content_hash=content_hash,
            record_state=record_state,
            expires_at=expires_at,
        )

        self.session.add(record)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Almost certainly a duplicate content hash — federation writes
            # may race with local writes that already landed. The row is
            # immutable and content-addressed, so a duplicate is a no-op.
            await self.session.rollback()
            if content_hash is not None:
                existing = await self._fetch_by_hash(content_hash)
                if existing is not None:
                    return existing
            raise CRDTStoreError(f"memory write failed: {exc}") from exc
        await self.session.refresh(record)
        return record

    # ------------------------------------------------------------------ Internals

    def _resolve_hash_and_signature(
        self,
        *,
        body: CanonicalBody,
        keypair: KeyPair | None,
        precomputed_hash: str | None,
        precomputed_signature: str | None,
    ) -> tuple[str | None, str | None]:
        """Return the ``(content_hash, signature)`` pair to persist."""
        # Unsigned / legacy path.
        if keypair is None and precomputed_signature is None:
            # Callers may still want a hash (e.g. internal content-address
            # queries) but no signature. Accept that only when an author_did
            # is declared so the hash anchors to someone's canonical body.
            if precomputed_hash is None and body.author_did is None:
                return None, None
            if precomputed_hash is not None:
                expected = compute_content_hash(body)
                if precomputed_hash != expected:
                    raise SignatureMismatchError(
                        f"precomputed_hash {precomputed_hash!r} does not match canonical body hash {expected!r}"
                    )
                return precomputed_hash, None
            return compute_content_hash(body), None

        # Signed path — we must produce both a hash and a signature.
        computed_hash = compute_content_hash(body)
        if precomputed_hash is not None and precomputed_hash != computed_hash:
            raise SignatureMismatchError(
                f"precomputed_hash {precomputed_hash!r} does not match canonical body hash {computed_hash!r}"
            )

        if keypair is not None:
            # We hold the private key, so we sign the body ourselves.
            signature_bytes = sign_bytes(body.to_signable_bytes(), keypair.private_seed)
            signature = encode_multibase(signature_bytes)
            # If caller also supplied a signature, both must agree (Ed25519 is
            # deterministic, so they should).
            if precomputed_signature is not None and precomputed_signature != signature:
                raise SignatureMismatchError("precomputed_signature disagrees with freshly-computed signature")
        else:
            # No private key, but caller supplied a signature — verify it.
            assert precomputed_signature is not None
            if body.author_did is None:
                raise UnknownAuthorError("cannot verify a signature without an author_did declaring the public key")
            public_bytes = _public_key_for_did(body.author_did)
            try:
                signature_bytes = decode_multibase(precomputed_signature)
            except Exception as exc:
                raise SignatureMismatchError(f"signature is not a multibase-encoded value: {exc}") from exc
            if not verify_bytes(body.to_signable_bytes(), signature_bytes, public_bytes):
                raise SignatureMismatchError("Ed25519 signature did not verify against the canonical body")
            signature = precomputed_signature

        return computed_hash, signature

    async def _fetch_by_hash(self, content_hash: str) -> MemoryRecord | None:
        from sqlalchemy import select

        result = await self.session.execute(select(MemoryRecord).where(MemoryRecord.content_hash == content_hash))
        return result.scalar_one_or_none()


def _public_key_for_did(did: str) -> bytes:
    """Extract the Ed25519 public key material from a DID we can resolve inline.

    did:key and legacy did:nanda embed the public key in the identifier
    itself. did:web requires fetching a DID document, which the CRDT store
    does not do — callers handing us a did:web signature must pre-verify
    the signature externally and pass the record through in unsigned-but-
    hashed mode.
    """
    parsed = parse_did(did)
    if parsed.method == "key":
        return decode_did_key(did)
    if parsed.method == "nanda":
        return decode_did_nanda(did)
    raise UnknownAuthorError(
        f"CRDT store cannot resolve public key for did:{parsed.method}: inline; "
        "did:web authors must supply a pre-verified record (unsigned mode)."
    )
