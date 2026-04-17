"""Signed HTTP client for peer-to-peer CRDT sync.

Consumes the endpoints we exposed in :mod:`app.api.memory_sync` on a
**remote** MassClaw node, signing every request with the local instance
Ed25519 key so the remote node can verify authenticity via its own
:func:`app.crdt.peer_auth.verify_peer_request` gate.

The signed payload matches the server's canonical form exactly —
``<timestamp>|<METHOD>|<path>`` — so signature mismatches are always a
bug on this side, not a disagreement about what bytes to sign.

This client is the only piece of MassClaw that makes *outbound* HTTP
calls to peer nodes. All sync logic stays here; the gossip orchestrator
(:mod:`app.crdt.gossip`) composes these calls into reconciliation
rounds.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger
from app.crdt.hashing import HASH_MULTIBASE_PREFIX
from app.crdt.merkle import BUCKET_COUNT, MerkleSummary
from app.crdt.peer_auth import (
    PEER_DID_HEADER,
    PEER_SIG_HEADER,
    PEER_TS_HEADER,
    _canonical_bytes,
)
from app.identity.did import build_did_key
from app.identity.signer import KeyPair, encode_multibase, sign_bytes

logger = get_logger(__name__)

_DEFAULT_MAX_FETCH_HASHES = 100  # must match SyncService's cap.
_DEFAULT_USER_AGENT = "MassClaw-PeerClient/1.0"


def _default_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class PeerClientError(Exception):
    """Base class for peer-client failures."""


class PeerUnavailableError(PeerClientError):
    """Raised when a peer is unreachable or returns 5xx after retries."""


class PeerUnauthorizedError(PeerClientError):
    """Raised when a peer rejects our signed request (401/403)."""


class PeerInvalidResponseError(PeerClientError):
    """Raised when a peer returns something we can't parse."""


@dataclass(frozen=True)
class PeerRecord:
    """One signed memory record as returned by a peer.

    Field names mirror the JSON produced by the remote node's
    ``MemoryResponse.model_dump(by_alias=True)``; we normalise here so the
    rest of the sync pipeline sees Python-friendly attributes and not raw
    dicts.
    """

    content_hash: str
    workflow_id: str
    source_agent_id: str | None
    memory_type: str
    content: str
    confidence: float
    metadata: dict
    author_did: str | None
    parent_hashes: list[str]
    signature: str | None
    record_state: str
    raw: dict


class PeerClient:
    """HTTP client for one remote MassClaw node's /sync surface."""

    def __init__(
        self,
        *,
        base_url: str,
        keypair: KeyPair,
        timeout: httpx.Timeout | None = None,
        user_agent: str = _DEFAULT_USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._keypair = keypair
        self._did = build_did_key(keypair.public_bytes)
        self._timeout = timeout if timeout is not None else _default_timeout()
        self._user_agent = user_agent
        self._transport = transport  # For tests: httpx.MockTransport.

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def our_did(self) -> str:
        return self._did

    # ---------------------------------------------------------------- Summary

    async def fetch_summary(self, *, workflow_id: uuid.UUID | None = None) -> MerkleSummary:
        """Fetch the remote node's Merkle summary."""
        path = "/api/v1/memory/sync/summary"
        params: dict[str, str] = {}
        if workflow_id is not None:
            params["workflow_id"] = str(workflow_id)
        response = await self._request("GET", path, params=params)
        body = self._safe_json_object(response)
        try:
            buckets = body["buckets"]
            if not isinstance(buckets, list) or len(buckets) != BUCKET_COUNT:
                raise ValueError(f"unexpected bucket count: {len(buckets) if isinstance(buckets, list) else 'n/a'}")
            return MerkleSummary(
                root=str(body["root"]),
                buckets=list(buckets),
                record_count=int(body.get("record_count", 0)),
            )
        except Exception as exc:
            raise PeerInvalidResponseError(f"peer summary shape invalid: {exc}; body={body!r}") from exc

    # ---------------------------------------------------------------- Bucket

    async def fetch_bucket_hashes(
        self,
        index: int,
        *,
        workflow_id: uuid.UUID | None = None,
    ) -> list[str]:
        """Fetch the remote node's sorted hashes in one Merkle bucket."""
        if not 0 <= index < BUCKET_COUNT:
            raise ValueError(f"bucket index out of range: {index}")
        path = f"/api/v1/memory/sync/buckets/{index}"
        params: dict[str, str] = {}
        if workflow_id is not None:
            params["workflow_id"] = str(workflow_id)
        response = await self._request("GET", path, params=params)
        body = self._safe_json_object(response)
        hashes = body.get("hashes")
        if not isinstance(hashes, list):
            raise PeerInvalidResponseError(f"peer bucket hashes not a list: {body!r}")
        return [h for h in hashes if isinstance(h, str) and h.startswith(HASH_MULTIBASE_PREFIX)]

    # ---------------------------------------------------------------- Fetch

    async def fetch_records(self, hashes: list[str]) -> list[PeerRecord]:
        """Fetch up to ``_DEFAULT_MAX_FETCH_HASHES`` records by content hash."""
        if not hashes:
            return []
        if len(hashes) > _DEFAULT_MAX_FETCH_HASHES:
            raise ValueError(
                f"peer /sync/fetch accepts at most {_DEFAULT_MAX_FETCH_HASHES} hashes per call; got {len(hashes)}"
            )
        path = "/api/v1/memory/sync/fetch"
        response = await self._request("POST", path, json={"hashes": hashes})
        body = self._safe_json_object(response)
        raw_records = body.get("records")
        if not isinstance(raw_records, list):
            raise PeerInvalidResponseError(f"peer /sync/fetch records not a list: {body!r}")
        return [_coerce_record(r) for r in raw_records if isinstance(r, dict)]

    # ---------------------------------------------------------------- Internals

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict | list | None = None,
    ) -> httpx.Response:
        url = self._base_url + path
        headers = self._sign_headers(method, path)
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            headers={"User-Agent": self._user_agent, "Accept": "application/json"},
        ) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                raise PeerUnavailableError(f"{method} {url} unreachable: {exc}") from exc
        if response.status_code in (401, 403):
            raise PeerUnauthorizedError(
                f"peer rejected {method} {path} (HTTP {response.status_code}): {response.text[:500]}"
            )
        if 500 <= response.status_code < 600:
            raise PeerUnavailableError(
                f"peer returned {response.status_code} on {method} {path}: {response.text[:500]}"
            )
        if response.status_code >= 400:
            raise PeerInvalidResponseError(
                f"peer returned {response.status_code} on {method} {path}: {response.text[:500]}"
            )
        return response

    def _sign_headers(self, method: str, path: str) -> dict[str, str]:
        """Build the three-header peer-auth bundle the server expects."""
        ts = int(time.time())
        payload = _canonical_bytes(method, path, ts)
        signature = encode_multibase(sign_bytes(payload, self._keypair.private_seed))
        return {
            PEER_DID_HEADER: self._did,
            PEER_TS_HEADER: str(ts),
            PEER_SIG_HEADER: signature,
        }

    @staticmethod
    def _safe_json_object(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except Exception as exc:
            raise PeerInvalidResponseError(f"peer returned non-JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise PeerInvalidResponseError(f"peer returned non-object JSON: {type(body).__name__}")
        return body


def _coerce_record(raw: dict) -> PeerRecord:
    """Convert a JSON record dict into a :class:`PeerRecord`."""
    content_hash = raw.get("hash") or raw.get("content_hash")
    if not isinstance(content_hash, str) or not content_hash:
        raise PeerInvalidResponseError(f"peer record missing 'hash' field: {raw!r}")
    return PeerRecord(
        content_hash=content_hash,
        workflow_id=str(raw.get("workflow_id", "")),
        source_agent_id=str(raw["source_agent_id"]) if raw.get("source_agent_id") else None,
        memory_type=str(raw.get("memory_type", "result")),
        content=str(raw.get("content", "")),
        confidence=float(raw.get("confidence", 0.0)),
        metadata=dict(raw.get("metadata") or {}),
        author_did=(raw.get("author_did") or None),
        parent_hashes=list(raw.get("parent_hashes") or []),
        signature=(raw.get("signature") or None),
        record_state=str(raw.get("record_state", "active")),
        raw=raw,
    )
