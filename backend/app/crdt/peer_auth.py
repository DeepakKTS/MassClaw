"""Peer-to-peer authentication for CRDT sync endpoints.

Two MassClaw nodes exchanging gossip must prove they are who they say
they are. The protocol is:

1. The caller signs ``<timestamp>|<http_method>|<path>`` with its
   instance Ed25519 key.
2. The caller attaches three headers:
     X-Peer-DID        — the caller's did:key / did:nanda identifier.
     X-Peer-Timestamp  — integer Unix seconds when the signature was made.
     X-Peer-Signature  — multibase (``z``-prefix) Ed25519 signature.
3. The server rejects the request unless:
     a. All three headers are present.
     b. The DID is in the configured peer whitelist (or the whitelist is
        empty, in which case any valid signature is accepted — use only
        for local federation tests).
     c. The timestamp is within ``±PEER_AUTH_WINDOW_SECONDS`` of ``now``.
     d. The signature verifies against the public key embedded in the DID.

This gives us:

- **Replay resistance** via the timestamp window.
- **Tamper resistance** via the signed method+path.
- **Source authenticity** via the DID's public key.

The auth helper is deliberately stateless so it composes with the
session-cookie auth used for the NANDA Index client (which is a
different surface).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.core.logging import get_logger
from app.identity.did import MalformedDIDError, parse_did, public_key_from_did
from app.identity.signer import decode_multibase, verify_bytes

logger = get_logger(__name__)

PEER_AUTH_WINDOW_SECONDS = 60
PEER_DID_HEADER = "X-Peer-DID"
PEER_TS_HEADER = "X-Peer-Timestamp"
PEER_SIG_HEADER = "X-Peer-Signature"


@dataclass(frozen=True)
class VerifiedPeer:
    """Result of a successful peer-auth verification."""

    did: str
    timestamp: int


def _auth_error(message: str, next_steps: list[str]) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": "peer_auth_failed",
            "message": message,
            "next_steps": next_steps,
        },
    )


def _canonical_bytes(method: str, path: str, timestamp: int) -> bytes:
    """Return the exact bytes peers must sign."""
    return f"{timestamp}|{method.upper()}|{path}".encode()


def _allowed_peer_dids() -> set[str]:
    """Return the set of peer DIDs explicitly allowed by config.

    Empty set → 'any valid signature accepted' (local-dev mode).
    """
    settings = get_settings()
    whitelist: list[str] = []
    raw = getattr(settings, "massclaw_peer_allowlist", "") or ""
    for item in raw.split(","):
        entry = item.strip()
        if entry:
            whitelist.append(entry)
    return set(whitelist)


def verify_peer_request(
    *,
    method: str,
    path: str,
    did: str | None,
    timestamp: str | None,
    signature: str | None,
    now: int | None = None,
) -> VerifiedPeer:
    """Validate a peer-signed sync request.

    Raises :class:`fastapi.HTTPException` (401) when the request fails any
    of the auth checks. Designed so the handler body can rely on receiving
    a verified :class:`VerifiedPeer` and nothing else.
    """
    if not did or not timestamp or not signature:
        raise _auth_error(
            "missing peer-auth headers",
            [
                f"supply {PEER_DID_HEADER}, {PEER_TS_HEADER}, and {PEER_SIG_HEADER} headers",
                "the signature must be Ed25519 over '<timestamp>|<METHOD>|<path>'",
            ],
        )

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise _auth_error(
            f"{PEER_TS_HEADER} must be an integer Unix timestamp",
            [f"received {timestamp!r}"],
        ) from exc

    current = now if now is not None else int(time.time())
    if abs(current - ts) > PEER_AUTH_WINDOW_SECONDS:
        raise _auth_error(
            "timestamp outside accepted window",
            [
                f"allowed window is ±{PEER_AUTH_WINDOW_SECONDS}s",
                f"received ts={ts} server_now={current}",
                "synchronise your clock (NTP) or re-sign",
            ],
        )

    allowed = _allowed_peer_dids()
    if allowed and did not in allowed:
        raise _auth_error(
            "peer DID is not in this node's allowlist",
            [
                f"received {did!r}",
                "ask the node operator to add you to MASSCLAW_PEER_ALLOWLIST",
            ],
        )

    try:
        public_bytes = public_key_from_did(did)
    except MalformedDIDError as exc:
        raise _auth_error(
            f"cannot verify peer DID: {exc}",
            [
                "use did:key: or did:nanda: — methods whose public key is embedded in the identifier",
                "did:web: peers must provide a pre-signed JWT (not supported on sync endpoints)",
            ],
        ) from exc

    try:
        sig_bytes = decode_multibase(signature)
    except Exception as exc:
        raise _auth_error(
            "signature is not a valid multibase-encoded value",
            [f"expected a 'z'-prefixed base58btc string, got {signature!r}"],
        ) from exc

    payload = _canonical_bytes(method, path, ts)
    if not verify_bytes(payload, sig_bytes, public_bytes):
        raise _auth_error(
            "signature did not verify",
            [
                "ensure the signed payload is exactly '<ts>|<METHOD>|<path>' with no whitespace",
                "confirm the DID's public key matches the signing key",
            ],
        )

    try:
        parse_did(did)
    except MalformedDIDError as exc:
        raise _auth_error(f"malformed DID shape: {exc}", []) from exc

    return VerifiedPeer(did=did, timestamp=ts)


async def require_verified_peer(
    request: Request,
    x_peer_did: str | None = Header(default=None, alias=PEER_DID_HEADER),
    x_peer_timestamp: str | None = Header(default=None, alias=PEER_TS_HEADER),
    x_peer_signature: str | None = Header(default=None, alias=PEER_SIG_HEADER),
) -> VerifiedPeer:
    """FastAPI dependency — injects a :class:`VerifiedPeer` on success.

    Path is derived from ``request.url.path`` so callers must sign the
    full server-side path including any prefix. Query strings are NOT
    part of the signed payload to keep the signature stable across
    cache-busting parameters.
    """
    return verify_peer_request(
        method=request.method,
        path=request.url.path,
        did=x_peer_did,
        timestamp=x_peer_timestamp,
        signature=x_peer_signature,
    )
