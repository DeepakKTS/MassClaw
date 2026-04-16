"""Ed25519 signing primitives for AgentFacts and signed memory records.

Ed25519 is chosen because it is the curve NANDA uses for AgentFacts, it produces
compact 64-byte signatures, verification is fast, and it has no parameter-choice
footguns. Keys are serialised with multibase base58btc so they render the same
way as in the NANDA reference documents.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.identity._base58 import b58decode, b58encode

# Multibase base58btc prefix per RFC draft.
_MULTIBASE_BTC = "z"


class SignatureError(Exception):
    """Raised when signature verification fails or a key is malformed."""


@dataclass(frozen=True)
class KeyPair:
    """A raw Ed25519 keypair (32-byte seed + 32-byte public key)."""

    private_seed: bytes
    public_bytes: bytes

    def __post_init__(self) -> None:
        if len(self.private_seed) != 32:
            raise SignatureError("private_seed must be exactly 32 bytes")
        if len(self.public_bytes) != 32:
            raise SignatureError("public_bytes must be exactly 32 bytes")


def generate_keypair() -> KeyPair:
    """Generate a fresh Ed25519 keypair."""
    sk = Ed25519PrivateKey.generate()
    seed = sk.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    pk = sk.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return KeyPair(private_seed=seed, public_bytes=pk)


def load_private_key(seed: bytes) -> Ed25519PrivateKey:
    """Reconstruct an Ed25519 private key object from its 32-byte seed."""
    if len(seed) != 32:
        raise SignatureError("Ed25519 seed must be exactly 32 bytes")
    try:
        return Ed25519PrivateKey.from_private_bytes(seed)
    except ValueError as exc:
        raise SignatureError(f"invalid Ed25519 seed: {exc}") from exc


def load_public_key(pub: bytes) -> Ed25519PublicKey:
    """Reconstruct an Ed25519 public key object from its 32-byte body."""
    if len(pub) != 32:
        raise SignatureError("Ed25519 public key must be exactly 32 bytes")
    try:
        return Ed25519PublicKey.from_public_bytes(pub)
    except ValueError as exc:
        raise SignatureError(f"invalid Ed25519 public key: {exc}") from exc


def sign_bytes(message: bytes, private_seed: bytes) -> bytes:
    """Sign arbitrary bytes with the given Ed25519 private seed."""
    sk = load_private_key(private_seed)
    return sk.sign(message)


def verify_bytes(message: bytes, signature: bytes, public_bytes: bytes) -> bool:
    """Verify an Ed25519 signature. Returns True on success, False otherwise.

    Never raises on a simple bad signature — the caller should check the
    boolean return value. Raises SignatureError only when the key itself
    is malformed.
    """
    pk = load_public_key(public_bytes)
    try:
        pk.verify(signature, message)
        return True
    except InvalidSignature:
        return False


def encode_multibase(raw: bytes) -> str:
    """Encode raw bytes with multibase base58btc (z + base58)."""
    return _MULTIBASE_BTC + b58encode(raw)


def decode_multibase(encoded: str) -> bytes:
    """Decode a multibase-encoded string. Only base58btc is supported."""
    if not encoded or encoded[0] != _MULTIBASE_BTC:
        raise SignatureError(f"unsupported multibase prefix: expected '{_MULTIBASE_BTC}' (base58btc)")
    return b58decode(encoded[1:])


def public_key_multibase(public_bytes: bytes) -> str:
    """Return a multibase-encoded representation of an Ed25519 public key."""
    if len(public_bytes) != 32:
        raise SignatureError("Ed25519 public key must be exactly 32 bytes")
    return encode_multibase(public_bytes)
