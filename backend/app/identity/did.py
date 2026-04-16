"""NANDA-style decentralised identifiers (DIDs).

A MassClaw agent's DID is derived from its Ed25519 public key:

    did:nanda:<multibase-base58btc-encoded public key>

This lets any holder of the DID verify signatures without fetching an
external document, and it keeps the string stable across infrastructure moves.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.identity.signer import decode_multibase, public_key_multibase

DID_METHOD = "nanda"
_DID_PREFIX = f"did:{DID_METHOD}:"


class MalformedDIDError(ValueError):
    """Raised when parsing a malformed DID string."""


@dataclass(frozen=True)
class ParsedDID:
    method: str
    identifier: str

    def as_str(self) -> str:
        return f"did:{self.method}:{self.identifier}"


def build_did_from_public_key(public_bytes: bytes) -> str:
    """Return the canonical NANDA DID for the given Ed25519 public key."""
    return _DID_PREFIX + public_key_multibase(public_bytes)


def parse_did(did: str) -> ParsedDID:
    """Parse a did:nanda:<id> string.

    Other methods are parsed but not executed — callers can match on
    ``result.method == DID_METHOD`` for NANDA-specific behaviour.
    """
    if not isinstance(did, str):
        raise MalformedDIDError(f"DID must be a string, got {type(did).__name__}")
    if not did.startswith("did:"):
        raise MalformedDIDError(f"DID must start with 'did:', got {did!r}")
    parts = did.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise MalformedDIDError(f"DID must have form 'did:<method>:<id>', got {did!r}")
    return ParsedDID(method=parts[1], identifier=parts[2])


def public_key_from_did(did: str) -> bytes:
    """Extract the Ed25519 public key embedded in a did:nanda DID."""
    parsed = parse_did(did)
    if parsed.method != DID_METHOD:
        raise MalformedDIDError(f"only did:{DID_METHOD}: is supported here, got did:{parsed.method}:")
    try:
        pub = decode_multibase(parsed.identifier)
    except Exception as exc:
        raise MalformedDIDError(f"cannot decode DID identifier: {exc}") from exc
    if len(pub) != 32:
        raise MalformedDIDError(f"embedded key must be 32 bytes (Ed25519 public), got {len(pub)}")
    return pub
