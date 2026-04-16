"""Decentralised Identifiers accepted by MassClaw.

The NANDA ecosystem settles on two DID methods for agent identity:

- ``did:key:<multibase>`` — the W3C-standard way to embed a raw Ed25519
  public key in a DID. The identifier is a multibase-encoded octet string
  starting with the ``ed25519-pub`` multicodec prefix (``0xed, 0x01``). No
  external resolution is required; the DID itself is the verification anchor.

- ``did:web:<host>[/path]`` — the public-key material lives in a DID
  Document hosted at ``https://<host>/.well-known/did.json`` (or
  ``https://<host>/<path>/did.json``). Primary method for enterprise
  agents whose verification anchor is a DNS domain.

MassClaw also accepts ``did:nanda:<multibase>`` for legacy compatibility —
our pre-v1 AgentFacts documents used this method. It has the same internal
format as ``did:key`` (multibase-encoded Ed25519 pub) but a different method
name, so we translate between them when interoperating with v1 consumers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.identity.signer import decode_multibase, encode_multibase

DID_METHOD_KEY = "key"
DID_METHOD_WEB = "web"
DID_METHOD_NANDA_LEGACY = "nanda"
DID_METHOD = DID_METHOD_NANDA_LEGACY  # Back-compat re-export for older callers.

# Multicodec prefix for Ed25519 public keys per
# https://github.com/multiformats/multicodec (``ed25519-pub`` → 0xED01).
_ED25519_MULTICODEC_PREFIX = b"\xed\x01"


class MalformedDIDError(ValueError):
    """Raised when parsing a malformed DID string."""


@dataclass(frozen=True)
class ParsedDID:
    method: str
    identifier: str

    def as_str(self) -> str:
        return f"did:{self.method}:{self.identifier}"


def parse_did(did: str) -> ParsedDID:
    """Parse a ``did:<method>:<identifier>`` string.

    The method is lowercased; the identifier preserves its original casing.
    Path components (``/foo/bar``) are kept in the identifier when present.
    """
    if not isinstance(did, str):
        raise MalformedDIDError(f"DID must be a string, got {type(did).__name__}")
    if not did.startswith("did:"):
        raise MalformedDIDError(f"DID must start with 'did:', got {did!r}")
    parts = did.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise MalformedDIDError(f"DID must have form 'did:<method>:<id>', got {did!r}")
    method, identifier = parts[1].lower(), parts[2]
    return ParsedDID(method=method, identifier=identifier)


# ---------------------------------------------------------------- did:key


def build_did_key(public_bytes: bytes) -> str:
    """Build a ``did:key`` from a raw 32-byte Ed25519 public key.

    Format: ``did:key:z<base58btc(multicodec-prefix || pubkey)>``.
    """
    if len(public_bytes) != 32:
        raise MalformedDIDError(f"Ed25519 public key must be 32 bytes, got {len(public_bytes)}")
    encoded = encode_multibase(_ED25519_MULTICODEC_PREFIX + public_bytes)
    return f"did:{DID_METHOD_KEY}:{encoded}"


def decode_did_key(did: str) -> bytes:
    """Extract the Ed25519 public key embedded in a did:key DID."""
    parsed = parse_did(did)
    if parsed.method != DID_METHOD_KEY:
        raise MalformedDIDError(f"expected did:{DID_METHOD_KEY}:, got did:{parsed.method}:")
    try:
        raw = decode_multibase(parsed.identifier)
    except Exception as exc:
        raise MalformedDIDError(f"cannot decode did:key identifier: {exc}") from exc
    if not raw.startswith(_ED25519_MULTICODEC_PREFIX):
        raise MalformedDIDError("did:key identifier is not an ed25519-pub multicodec (must start with 0xED 0x01)")
    body = raw[len(_ED25519_MULTICODEC_PREFIX) :]
    if len(body) != 32:
        raise MalformedDIDError(f"did:key Ed25519 body must be 32 bytes, got {len(body)}")
    return body


# ---------------------------------------------------------------- did:web


def build_did_web(host: str, path: str | None = None) -> str:
    """Build a ``did:web`` from an HTTPS host and optional path.

    Example: ``build_did_web("massclaw.example")`` → ``did:web:massclaw.example``.
    With a path: ``build_did_web("massclaw.example", "agents/scheduler")`` →
    ``did:web:massclaw.example:agents:scheduler`` (colons replace slashes per
    the did:web spec).
    """
    if not host or "/" in host or ":" in host:
        raise MalformedDIDError(f"did:web host must be a plain DNS name, got {host!r}")
    identifier = host
    if path:
        cleaned = path.strip("/").replace("/", ":")
        if cleaned:
            identifier = f"{host}:{cleaned}"
    return f"did:{DID_METHOD_WEB}:{identifier}"


def did_web_to_url(did: str) -> str:
    """Return the ``did.json`` URL where a ``did:web`` DID Document is served."""
    parsed = parse_did(did)
    if parsed.method != DID_METHOD_WEB:
        raise MalformedDIDError(f"expected did:{DID_METHOD_WEB}:, got did:{parsed.method}:")
    parts = parsed.identifier.split(":")
    host = parts[0]
    if not host:
        raise MalformedDIDError(f"did:web missing host in {did!r}")
    if len(parts) == 1:
        return f"https://{host}/.well-known/did.json"
    path = "/".join(parts[1:])
    return f"https://{host}/{path}/did.json"


# ---------------------------------------------------------------- did:nanda (legacy)


def build_did_nanda(public_bytes: bytes) -> str:
    """Legacy MassClaw DID method — multibase Ed25519 pub, no multicodec prefix.

    Kept for backward compatibility with pre-v1 AgentFacts consumers. New code
    should prefer :func:`build_did_key`.
    """
    if len(public_bytes) != 32:
        raise MalformedDIDError(f"Ed25519 public key must be 32 bytes, got {len(public_bytes)}")
    return f"did:{DID_METHOD_NANDA_LEGACY}:{encode_multibase(public_bytes)}"


def decode_did_nanda(did: str) -> bytes:
    parsed = parse_did(did)
    if parsed.method != DID_METHOD_NANDA_LEGACY:
        raise MalformedDIDError(f"expected did:{DID_METHOD_NANDA_LEGACY}:, got did:{parsed.method}:")
    try:
        body = decode_multibase(parsed.identifier)
    except Exception as exc:
        raise MalformedDIDError(f"cannot decode did:nanda identifier: {exc}") from exc
    if len(body) != 32:
        raise MalformedDIDError(f"did:nanda identifier must decode to 32 bytes (Ed25519 pub), got {len(body)}")
    return body


# ---------------------------------------------------------------- back-compat shims


def build_did_from_public_key(public_bytes: bytes) -> str:
    """Default DID constructor for new MassClaw code — produces ``did:key``.

    Kept as the public builder so existing callers (pre-v1) don't need to
    change; callers that specifically want legacy ``did:nanda:`` output
    should use :func:`build_did_nanda` explicitly.
    """
    return build_did_key(public_bytes)


def public_key_from_did(did: str) -> bytes:
    """Extract the Ed25519 public key embedded in a DID where possible.

    Works for did:key and did:nanda. ``did:web:`` DIDs require network
    resolution of the DID Document — callers must use the appropriate
    resolver; this function raises for web DIDs because we cannot produce
    a key without that step.
    """
    parsed = parse_did(did)
    if parsed.method == DID_METHOD_KEY:
        return decode_did_key(did)
    if parsed.method == DID_METHOD_NANDA_LEGACY:
        return decode_did_nanda(did)
    raise MalformedDIDError(f"cannot extract Ed25519 public key from did:{parsed.method}: without a resolver")
