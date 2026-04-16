"""Unit tests for :class:`CRDTStore`'s sign/verify/hash resolution logic.

These tests exercise the pure crypto / verification paths — no database
access. Integration with real Postgres (dedupe on duplicate hash, actual
persistence) lives in ``tests/integration/test_crdt_store.py`` where the
session fixture is available.
"""

from __future__ import annotations

import uuid

import pytest

from app.crdt.hashing import build_canonical_body, compute_content_hash
from app.crdt.store import (
    CRDTStore,
    SignatureMismatchError,
    UnknownAuthorError,
)
from app.identity.did import build_did_key, build_did_nanda, build_did_web
from app.identity.signer import encode_multibase, generate_keypair, sign_bytes
from app.models.base import MemoryType


def _store() -> CRDTStore:
    """Build a store whose session is never touched — we only use the resolver."""
    return CRDTStore(session=None)  # type: ignore[arg-type]


def _body_args(**overrides):
    defaults = dict(
        workflow_id=uuid.uuid4(),
        memory_type=MemoryType.RESULT,
        content="deadline: April 30",
        confidence=0.9,
        metadata={"domain": "planning"},
        parent_hashes=[],
        author_did=None,
        source_agent_id=None,
    )
    defaults.update(overrides)
    return defaults


def _canonical(**overrides):
    return build_canonical_body(**_body_args(**overrides))


# ---------------------------------------------------------------- unsigned path


class TestUnsignedPath:
    def test_returns_none_hash_and_signature_when_no_author(self) -> None:
        store = _store()
        body = _canonical(author_did=None)
        hash_, sig = store._resolve_hash_and_signature(
            body=body, keypair=None, precomputed_hash=None, precomputed_signature=None
        )
        assert hash_ is None
        assert sig is None

    def test_computes_hash_when_author_did_present(self) -> None:
        store = _store()
        did = build_did_key(generate_keypair().public_bytes)
        body = _canonical(author_did=did)
        hash_, sig = store._resolve_hash_and_signature(
            body=body, keypair=None, precomputed_hash=None, precomputed_signature=None
        )
        assert hash_ == compute_content_hash(body)
        assert sig is None

    def test_accepts_matching_precomputed_hash(self) -> None:
        store = _store()
        did = build_did_key(generate_keypair().public_bytes)
        body = _canonical(author_did=did)
        expected = compute_content_hash(body)
        hash_, sig = store._resolve_hash_and_signature(
            body=body,
            keypair=None,
            precomputed_hash=expected,
            precomputed_signature=None,
        )
        assert hash_ == expected
        assert sig is None

    def test_rejects_mismatching_precomputed_hash(self) -> None:
        store = _store()
        did = build_did_key(generate_keypair().public_bytes)
        body = _canonical(author_did=did)
        with pytest.raises(SignatureMismatchError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=None,
                precomputed_hash="zWRONGHASH" + "a" * 20,
                precomputed_signature=None,
            )


# ---------------------------------------------------------------- signed path (we have the key)


class TestSignedPathWithKeypair:
    def test_signs_body_and_returns_hash_signature_pair(self) -> None:
        store = _store()
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        body = _canonical(author_did=did)

        hash_, sig = store._resolve_hash_and_signature(
            body=body, keypair=kp, precomputed_hash=None, precomputed_signature=None
        )
        assert hash_ == compute_content_hash(body)
        assert sig is not None
        assert sig.startswith("z")
        # Verify the signature we produced.
        from app.identity.signer import decode_multibase, verify_bytes

        assert verify_bytes(body.to_signable_bytes(), decode_multibase(sig), kp.public_bytes)

    def test_signatures_are_deterministic(self) -> None:
        store = _store()
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        body = _canonical(author_did=did)
        _, sig1 = store._resolve_hash_and_signature(
            body=body, keypair=kp, precomputed_hash=None, precomputed_signature=None
        )
        _, sig2 = store._resolve_hash_and_signature(
            body=body, keypair=kp, precomputed_hash=None, precomputed_signature=None
        )
        assert sig1 == sig2  # Ed25519 is deterministic.

    def test_rejects_precomputed_hash_mismatch_even_with_keypair(self) -> None:
        store = _store()
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        body = _canonical(author_did=did)
        with pytest.raises(SignatureMismatchError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=kp,
                precomputed_hash="zWRONG" + "a" * 30,
                precomputed_signature=None,
            )

    def test_rejects_precomputed_signature_mismatch(self) -> None:
        store = _store()
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        body = _canonical(author_did=did)
        bogus_sig = encode_multibase(b"\x00" * 64)
        with pytest.raises(SignatureMismatchError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=kp,
                precomputed_hash=None,
                precomputed_signature=bogus_sig,
            )


# ---------------------------------------------------------------- signed path (caller supplies signature)


class TestSignedPathPrecomputed:
    def test_verifies_precomputed_signature(self) -> None:
        store = _store()
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        body = _canonical(author_did=did)
        signature = encode_multibase(sign_bytes(body.to_signable_bytes(), kp.private_seed))

        hash_, sig = store._resolve_hash_and_signature(
            body=body,
            keypair=None,
            precomputed_hash=compute_content_hash(body),
            precomputed_signature=signature,
        )
        assert hash_ == compute_content_hash(body)
        assert sig == signature

    def test_rejects_forged_signature(self) -> None:
        store = _store()
        real_kp = generate_keypair()
        did = build_did_key(real_kp.public_bytes)
        body = _canonical(author_did=did)
        other_kp = generate_keypair()
        forged_sig = encode_multibase(sign_bytes(body.to_signable_bytes(), other_kp.private_seed))
        with pytest.raises(SignatureMismatchError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=None,
                precomputed_hash=compute_content_hash(body),
                precomputed_signature=forged_sig,
            )

    def test_rejects_signature_without_author_did(self) -> None:
        store = _store()
        body = _canonical(author_did=None)
        with pytest.raises(UnknownAuthorError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=None,
                precomputed_hash=None,
                precomputed_signature="zsomething",
            )

    def test_rejects_malformed_multibase_signature(self) -> None:
        store = _store()
        did = build_did_key(generate_keypair().public_bytes)
        body = _canonical(author_did=did)
        with pytest.raises(SignatureMismatchError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=None,
                precomputed_hash=None,
                precomputed_signature="not-multibase",
            )


# ---------------------------------------------------------------- DID method coverage


class TestDidMethodCoverage:
    def test_did_nanda_signature_verifies(self) -> None:
        store = _store()
        kp = generate_keypair()
        did = build_did_nanda(kp.public_bytes)
        body = _canonical(author_did=did)
        signature = encode_multibase(sign_bytes(body.to_signable_bytes(), kp.private_seed))
        _, sig = store._resolve_hash_and_signature(
            body=body,
            keypair=None,
            precomputed_hash=None,
            precomputed_signature=signature,
        )
        assert sig == signature

    def test_did_web_rejected_in_signed_mode(self) -> None:
        store = _store()
        body = _canonical(author_did=build_did_web("example.com"))
        with pytest.raises(UnknownAuthorError):
            store._resolve_hash_and_signature(
                body=body,
                keypair=None,
                precomputed_hash=None,
                precomputed_signature="z" + "a" * 80,
            )
