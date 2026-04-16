"""Unit tests for peer-to-peer Ed25519 auth on CRDT sync endpoints."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.crdt.peer_auth import (
    PEER_AUTH_WINDOW_SECONDS,
    _canonical_bytes,
    verify_peer_request,
)
from app.identity.did import build_did_key, build_did_web
from app.identity.signer import encode_multibase, generate_keypair, sign_bytes


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    """Each test starts with a fresh ``get_settings()`` cache and no allowlist.

    Without this, allowlist tests leak their env var into subsequent tests
    because the ``@lru_cache`` on ``get_settings`` outlives the monkeypatch
    teardown.
    """
    monkeypatch.delenv("MASSCLAW_PEER_ALLOWLIST", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _sign(method: str, path: str, ts: int, keypair) -> str:
    return encode_multibase(sign_bytes(_canonical_bytes(method, path, ts), keypair.private_seed))


class TestVerifyPeerRequest:
    def test_valid_request_returns_verified_peer(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        sig = _sign("GET", "/api/v1/memory/sync/summary", ts, kp)

        peer = verify_peer_request(
            method="GET",
            path="/api/v1/memory/sync/summary",
            did=did,
            timestamp=str(ts),
            signature=sig,
            now=ts,
        )
        assert peer.did == did
        assert peer.timestamp == ts

    def test_missing_headers_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/api/v1/memory/sync/summary",
                did=None,
                timestamp="123",
                signature="zabc",
            )
        assert exc.value.status_code == 401
        assert "missing" in exc.value.detail["message"]

    def test_non_numeric_timestamp_rejected(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/foo",
                did=did,
                timestamp="not-a-number",
                signature="zabc",
            )
        assert exc.value.status_code == 401
        assert "integer" in exc.value.detail["message"].lower()

    def test_timestamp_outside_window_rejected(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        sig = _sign("GET", "/foo", ts, kp)
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/foo",
                did=did,
                timestamp=str(ts),
                signature=sig,
                now=ts + PEER_AUTH_WINDOW_SECONDS + 1,
            )
        assert exc.value.status_code == 401
        assert "window" in exc.value.detail["message"]

    def test_timestamp_at_window_boundary_accepted(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        sig = _sign("GET", "/foo", ts, kp)
        # Exactly at the edge is still in.
        verify_peer_request(
            method="GET",
            path="/foo",
            did=did,
            timestamp=str(ts),
            signature=sig,
            now=ts + PEER_AUTH_WINDOW_SECONDS,
        )

    def test_bad_signature_rejected(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        bogus_sig = encode_multibase(b"\x00" * 64)
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/foo",
                did=did,
                timestamp=str(ts),
                signature=bogus_sig,
                now=ts,
            )
        assert exc.value.status_code == 401
        assert "did not verify" in exc.value.detail["message"]

    def test_wrong_method_in_signature_rejected(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        # Peer signs GET but the server sees POST → different canonical bytes.
        sig = _sign("GET", "/foo", ts, kp)
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="POST",
                path="/foo",
                did=did,
                timestamp=str(ts),
                signature=sig,
                now=ts,
            )
        assert exc.value.status_code == 401

    def test_wrong_path_in_signature_rejected(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        sig = _sign("GET", "/foo", ts, kp)
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/bar",
                did=did,
                timestamp=str(ts),
                signature=sig,
                now=ts,
            )
        assert exc.value.status_code == 401

    def test_malformed_multibase_signature_rejected(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/foo",
                did=did,
                timestamp=str(ts),
                signature="not-multibase",
                now=ts,
            )
        assert exc.value.status_code == 401
        assert "multibase" in exc.value.detail["message"]

    def test_did_web_rejected_on_sync_endpoint(self) -> None:
        """did:web peers have to pre-verify externally — we can't fetch the DID doc from the hot path."""
        ts = int(time.time())
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/foo",
                did=build_did_web("example.com"),
                timestamp=str(ts),
                signature="zabc",
                now=ts,
            )
        assert exc.value.status_code == 401

    def test_canonical_bytes_format(self) -> None:
        """The signed payload is exactly '<ts>|<METHOD>|<path>'."""
        assert _canonical_bytes("get", "/foo", 123) == b"123|GET|/foo"
        assert _canonical_bytes("POST", "/a/b", 456) == b"456|POST|/a/b"

    def test_method_is_uppercased_before_signing(self) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        # Peer signs "get" lowercase; server normalises to GET.
        sig = _sign("get", "/foo", ts, kp)
        peer = verify_peer_request(
            method="GET",
            path="/foo",
            did=did,
            timestamp=str(ts),
            signature=sig,
            now=ts,
        )
        assert peer.did == did


class TestAllowlist:
    def test_when_allowlist_empty_any_valid_peer_accepted(self, monkeypatch) -> None:
        monkeypatch.setenv("MASSCLAW_PEER_ALLOWLIST", "")
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        ts = int(time.time())
        sig = _sign("GET", "/foo", ts, kp)
        peer = verify_peer_request(
            method="GET",
            path="/foo",
            did=did,
            timestamp=str(ts),
            signature=sig,
            now=ts,
        )
        assert peer.did == did

    def test_allowlist_rejects_unknown_peer(self, monkeypatch) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)

        # Allowlist contains a DIFFERENT DID.
        other_kp = generate_keypair()
        allowed_did = build_did_key(other_kp.public_bytes)
        monkeypatch.setenv("MASSCLAW_PEER_ALLOWLIST", allowed_did)
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        ts = int(time.time())
        sig = _sign("GET", "/foo", ts, kp)
        with pytest.raises(HTTPException) as exc:
            verify_peer_request(
                method="GET",
                path="/foo",
                did=did,
                timestamp=str(ts),
                signature=sig,
                now=ts,
            )
        assert exc.value.status_code == 401
        assert "allowlist" in exc.value.detail["message"]

    def test_allowlist_accepts_listed_peer(self, monkeypatch) -> None:
        kp = generate_keypair()
        did = build_did_key(kp.public_bytes)
        monkeypatch.setenv("MASSCLAW_PEER_ALLOWLIST", did)
        from app.config import get_settings

        get_settings.cache_clear()  # type: ignore[attr-defined]

        ts = int(time.time())
        sig = _sign("GET", "/foo", ts, kp)
        peer = verify_peer_request(
            method="GET",
            path="/foo",
            did=did,
            timestamp=str(ts),
            signature=sig,
            now=ts,
        )
        assert peer.did == did
