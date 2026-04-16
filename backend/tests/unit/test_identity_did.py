"""Unit tests for NANDA DID parsing and derivation."""

from __future__ import annotations

import pytest

from app.identity.did import (
    DID_METHOD,
    MalformedDIDError,
    build_did_from_public_key,
    parse_did,
    public_key_from_did,
)
from app.identity.signer import generate_keypair


class TestBuildDID:
    def test_roundtrip(self) -> None:
        kp = generate_keypair()
        did = build_did_from_public_key(kp.public_bytes)
        assert did.startswith(f"did:{DID_METHOD}:z")
        assert public_key_from_did(did) == kp.public_bytes

    def test_unique_keys_produce_unique_dids(self) -> None:
        dids = {build_did_from_public_key(generate_keypair().public_bytes) for _ in range(16)}
        assert len(dids) == 16


class TestParseDID:
    def test_basic_parse(self) -> None:
        parsed = parse_did("did:nanda:abc123")
        assert parsed.method == "nanda"
        assert parsed.identifier == "abc123"

    def test_parse_preserves_as_str_roundtrip(self) -> None:
        parsed = parse_did("did:example:xyz")
        assert parsed.as_str() == "did:example:xyz"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not-a-did",
            "did:",
            "did::foo",
            "did:nanda:",
            "nanda:z123",
        ],
    )
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(MalformedDIDError):
            parse_did(bad)

    def test_non_string_raises(self) -> None:
        with pytest.raises(MalformedDIDError):
            parse_did(12345)  # type: ignore[arg-type]


class TestPublicKeyExtraction:
    def test_only_nanda_method_supported(self) -> None:
        with pytest.raises(MalformedDIDError):
            public_key_from_did("did:example:zabc")

    def test_wrong_length_key_rejected(self) -> None:
        # Construct a DID whose identifier decodes to 4 bytes — not 32.
        from app.identity.signer import encode_multibase

        short_key = encode_multibase(b"abcd")
        with pytest.raises(MalformedDIDError):
            public_key_from_did(f"did:nanda:{short_key}")

    def test_malformed_identifier_rejected(self) -> None:
        with pytest.raises(MalformedDIDError):
            public_key_from_did("did:nanda:!!!not-base58!!!")
