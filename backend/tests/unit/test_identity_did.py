"""Unit tests for DID method parsing, building, and key extraction."""

from __future__ import annotations

import pytest

from app.identity.did import (
    MalformedDIDError,
    build_did_from_public_key,
    build_did_key,
    build_did_nanda,
    build_did_web,
    decode_did_key,
    decode_did_nanda,
    did_web_to_url,
    parse_did,
    public_key_from_did,
)
from app.identity.signer import generate_keypair


class TestParseDID:
    def test_basic(self) -> None:
        parsed = parse_did("did:key:z6Mk...")
        assert parsed.method == "key"
        assert parsed.identifier == "z6Mk..."

    def test_method_lowercased(self) -> None:
        assert parse_did("did:KEY:abc").method == "key"

    def test_identifier_case_preserved(self) -> None:
        parsed = parse_did("did:web:Example.COM")
        assert parsed.identifier == "Example.COM"

    @pytest.mark.parametrize(
        "bad",
        ["", "not-a-did", "did:", "did::foo", "did:nanda:", "nanda:z123"],
    )
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(MalformedDIDError):
            parse_did(bad)

    def test_non_string_raises(self) -> None:
        with pytest.raises(MalformedDIDError):
            parse_did(12345)  # type: ignore[arg-type]


class TestDidKey:
    def test_roundtrip(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        assert did.startswith("did:key:z")
        assert decode_did_key(did) == kp.public_bytes

    def test_includes_multicodec_prefix(self) -> None:
        from app.identity.signer import decode_multibase

        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        raw = decode_multibase(did.split(":", 2)[2])
        assert raw[:2] == b"\xed\x01"  # ed25519-pub multicodec.
        assert raw[2:] == kp.public_bytes

    def test_rejects_non_key_method(self) -> None:
        with pytest.raises(MalformedDIDError):
            decode_did_key("did:web:example.com")

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(MalformedDIDError):
            build_did_key(b"\x00" * 31)

    def test_rejects_missing_multicodec(self) -> None:
        # Build a did:key without the multicodec prefix — must be rejected.
        from app.identity.signer import encode_multibase

        raw_pubkey = b"\x00" * 32
        bad = f"did:key:{encode_multibase(raw_pubkey)}"
        with pytest.raises(MalformedDIDError):
            decode_did_key(bad)

    def test_uniqueness(self) -> None:
        keys = {build_did_key(generate_keypair().public_bytes) for _ in range(16)}
        assert len(keys) == 16


class TestDidWeb:
    def test_plain_host(self) -> None:
        assert build_did_web("massclaw.example") == "did:web:massclaw.example"

    def test_with_path(self) -> None:
        assert build_did_web("example.com", "agents/scheduler") == "did:web:example.com:agents:scheduler"

    def test_url_plain_host(self) -> None:
        assert did_web_to_url("did:web:example.com") == "https://example.com/.well-known/did.json"

    def test_url_with_path(self) -> None:
        assert did_web_to_url("did:web:example.com:agents:scheduler") == "https://example.com/agents/scheduler/did.json"

    def test_rejects_host_with_slash(self) -> None:
        with pytest.raises(MalformedDIDError):
            build_did_web("example.com/bad")

    def test_rejects_non_web_method(self) -> None:
        with pytest.raises(MalformedDIDError):
            did_web_to_url("did:key:z6Mk...")

    def test_rejects_empty_host_in_url(self) -> None:
        # Malformed identifier — "::foo" — shape validation catches this at parse_did.
        with pytest.raises(MalformedDIDError):
            did_web_to_url("did:web::foo")


class TestLegacyDidNanda:
    def test_roundtrip(self) -> None:
        kp = generate_keypair()
        did = build_did_nanda(kp.public_bytes)
        assert did.startswith("did:nanda:z")
        assert decode_did_nanda(did) == kp.public_bytes

    def test_rejects_non_nanda_method(self) -> None:
        with pytest.raises(MalformedDIDError):
            decode_did_nanda("did:key:z6Mk...")


class TestPublicKeyExtraction:
    def test_from_did_key(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        assert public_key_from_did(did) == kp.public_bytes

    def test_from_did_nanda_legacy(self) -> None:
        kp = generate_keypair()
        did = build_did_nanda(kp.public_bytes)
        assert public_key_from_did(did) == kp.public_bytes

    def test_did_web_requires_resolver(self) -> None:
        with pytest.raises(MalformedDIDError):
            public_key_from_did("did:web:example.com")


class TestDefaultBuilder:
    def test_default_produces_did_key(self) -> None:
        kp = generate_keypair()
        did = build_did_from_public_key(kp.public_bytes)
        assert did.startswith("did:key:z")
