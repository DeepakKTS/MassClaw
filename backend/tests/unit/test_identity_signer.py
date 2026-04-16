"""Unit tests for Ed25519 signing primitives and multibase encoding."""

from __future__ import annotations

import pytest

from app.identity._base58 import b58decode, b58encode
from app.identity.signer import (
    SignatureError,
    decode_multibase,
    encode_multibase,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_multibase,
    sign_bytes,
    verify_bytes,
)


class TestKeyGeneration:
    def test_keypair_has_correct_lengths(self) -> None:
        kp = generate_keypair()
        assert len(kp.private_seed) == 32
        assert len(kp.public_bytes) == 32

    def test_generated_keys_are_unique(self) -> None:
        keys = {generate_keypair().public_bytes for _ in range(16)}
        assert len(keys) == 16

    def test_keypair_rejects_wrong_length_seed(self) -> None:
        from app.identity.signer import KeyPair

        with pytest.raises(SignatureError):
            KeyPair(private_seed=b"\x00" * 16, public_bytes=b"\x00" * 32)

    def test_keypair_rejects_wrong_length_public(self) -> None:
        from app.identity.signer import KeyPair

        with pytest.raises(SignatureError):
            KeyPair(private_seed=b"\x00" * 32, public_bytes=b"\x00" * 20)


class TestSignAndVerify:
    def test_roundtrip_succeeds(self) -> None:
        kp = generate_keypair()
        msg = b"hello, NANDA"
        sig = sign_bytes(msg, kp.private_seed)
        assert verify_bytes(msg, sig, kp.public_bytes) is True

    def test_verification_rejects_wrong_message(self) -> None:
        kp = generate_keypair()
        sig = sign_bytes(b"original", kp.private_seed)
        assert verify_bytes(b"tampered", sig, kp.public_bytes) is False

    def test_verification_rejects_wrong_key(self) -> None:
        kp = generate_keypair()
        other = generate_keypair()
        sig = sign_bytes(b"msg", kp.private_seed)
        assert verify_bytes(b"msg", sig, other.public_bytes) is False

    def test_verification_rejects_truncated_signature(self) -> None:
        kp = generate_keypair()
        sig = sign_bytes(b"msg", kp.private_seed)
        assert verify_bytes(b"msg", sig[:-1], kp.public_bytes) is False

    def test_signature_length_is_64_bytes(self) -> None:
        kp = generate_keypair()
        sig = sign_bytes(b"anything", kp.private_seed)
        assert len(sig) == 64

    def test_same_message_and_key_produces_same_signature(self) -> None:
        """Ed25519 signatures are deterministic — verify this invariant."""
        kp = generate_keypair()
        msg = b"same message"
        sig1 = sign_bytes(msg, kp.private_seed)
        sig2 = sign_bytes(msg, kp.private_seed)
        assert sig1 == sig2

    def test_invalid_seed_raises(self) -> None:
        with pytest.raises(SignatureError):
            sign_bytes(b"msg", b"\x00" * 16)

    def test_invalid_public_key_raises(self) -> None:
        with pytest.raises(SignatureError):
            verify_bytes(b"msg", b"\x00" * 64, b"\x00" * 16)


class TestKeyLoading:
    def test_load_private_key_roundtrips(self) -> None:
        kp = generate_keypair()
        sk = load_private_key(kp.private_seed)
        assert sk is not None

    def test_load_public_key_roundtrips(self) -> None:
        kp = generate_keypair()
        pk = load_public_key(kp.public_bytes)
        assert pk is not None


class TestMultibase:
    def test_roundtrip(self) -> None:
        raw = b"\x01\x02\x03\x04\x05"
        assert decode_multibase(encode_multibase(raw)) == raw

    def test_roundtrip_empty(self) -> None:
        assert decode_multibase(encode_multibase(b"")) == b""

    def test_roundtrip_leading_zero_bytes(self) -> None:
        """Base58 must preserve leading zero bytes."""
        raw = b"\x00\x00\x00\xff"
        assert decode_multibase(encode_multibase(raw)) == raw

    def test_public_key_multibase_produces_z_prefix(self) -> None:
        kp = generate_keypair()
        encoded = public_key_multibase(kp.public_bytes)
        assert encoded.startswith("z")

    def test_public_key_multibase_rejects_bad_length(self) -> None:
        with pytest.raises(SignatureError):
            public_key_multibase(b"\x00" * 31)

    def test_decode_rejects_wrong_prefix(self) -> None:
        with pytest.raises(SignatureError):
            decode_multibase("f010203")

    def test_decode_rejects_empty_string(self) -> None:
        with pytest.raises(SignatureError):
            decode_multibase("")


class TestBase58Primitives:
    """Sanity checks on the low-level base58 codec."""

    def test_basic_roundtrip(self) -> None:
        data = b"hello world"
        assert b58decode(b58encode(data)) == data

    def test_leading_zero_byte_maps_to_leading_one(self) -> None:
        encoded = b58encode(b"\x00\x01")
        assert encoded.startswith("1")

    def test_decode_rejects_invalid_char(self) -> None:
        with pytest.raises(ValueError):
            b58decode("0O")  # zero and uppercase-O are deliberately excluded.
