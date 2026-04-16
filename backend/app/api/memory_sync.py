"""HTTP surface for peer-to-peer CRDT sync.

Three endpoints, all under ``/api/v1/memory/sync/``:

- ``GET  /summary``          — return this node's Merkle summary.
- ``GET  /buckets/{index}``  — return the sorted hashes in one bucket.
- ``POST /fetch``            — return the signed records for a set of hashes.

All three require the caller to present peer-auth headers
(``X-Peer-DID``, ``X-Peer-Timestamp``, ``X-Peer-Signature`` — see
:mod:`app.crdt.peer_auth`). When the node's ``MASSCLAW_PEER_ALLOWLIST``
setting is empty, any valid signature is accepted (local-dev mode);
when it is populated, only allowlisted DIDs can initiate sync.

Why three endpoints instead of one "sync everything" call? Because that
lets a peer incur O(log N) traffic per reconciliation round instead of
O(N): compare roots first (tiny), fetch diverging buckets only (small),
fetch only the records we're actually missing (bounded).
"""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.crdt.peer_auth import VerifiedPeer, require_verified_peer
from app.crdt.sync import SyncService, TooManyHashesRequested
from app.schemas.memory import MemoryResponse

logger = get_logger(__name__)

router = APIRouter()


@router.get(
    "/summary",
    summary="Merkle summary of this node's signed memory state.",
)
async def get_sync_summary(
    workflow_id: uuid.UUID | None = Query(
        default=None,
        description="Optional scope to a single workflow. Omitted → instance-wide summary.",
    ),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    peer: VerifiedPeer = Depends(require_verified_peer),
) -> dict[str, Any]:
    """Return the Merkle fingerprint of this node's ACTIVE signed records.

    Compare the returned ``root`` to your own. If they match, you're
    in sync. If they differ, inspect ``buckets[i]`` — the bucket indices
    whose digest differs are the ones worth pulling via
    ``GET /sync/buckets/{index}`` and then ``POST /sync/fetch``.

    **Response**

    ```json
    {
      "root":          "<hex sha256>",
      "buckets":       [null, "<hex>", null, ...],   # 256 entries
      "record_count":  42,
      "scope":         "workflow" | "instance"
    }
    ```
    """
    service = SyncService(session=session, redis=redis)
    summary = await service.summarise(workflow_id=workflow_id)
    logger.info(
        "sync_summary_served",
        peer_did=peer.did,
        scope="workflow" if workflow_id else "instance",
        root=summary.root[:12],
        records=summary.record_count,
    )
    return {
        "root": summary.root,
        "buckets": summary.buckets,
        "record_count": summary.record_count,
        "scope": "workflow" if workflow_id else "instance",
    }


@router.get(
    "/buckets/{bucket_index}",
    summary="Sorted content hashes in one Merkle bucket.",
)
async def get_sync_bucket(
    bucket_index: int,
    workflow_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    peer: VerifiedPeer = Depends(require_verified_peer),
) -> dict[str, Any]:
    """Return every content hash in bucket ``bucket_index`` (0..255).

    Callers diff the returned list against their own bucket, compute the
    set of hashes they are missing, and pass that subset to
    ``POST /sync/fetch``.

    Returns 400 for out-of-range bucket indices with the canonical
    ``{error, message, next_steps}`` envelope so stock clients self-recover.
    """
    service = SyncService(session=session, redis=redis)
    try:
        hashes = await service.bucket_hashes(bucket_index, workflow_id=workflow_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_bucket_index",
                "message": str(exc),
                "next_steps": ["bucket_index must be an integer in [0, 255]"],
            },
        ) from exc
    logger.info(
        "sync_bucket_served",
        peer_did=peer.did,
        bucket_index=bucket_index,
        hashes=len(hashes),
    )
    return {
        "bucket_index": bucket_index,
        "scope": "workflow" if workflow_id else "instance",
        "hashes": hashes,
    }


@router.post(
    "/fetch",
    summary="Fetch signed memory records by content hash.",
)
async def fetch_records_by_hash(
    payload: dict[str, Any] = Body(..., description='JSON body: {"hashes": ["z...", "z..."]}'),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    peer: VerifiedPeer = Depends(require_verified_peer),
) -> dict[str, Any]:
    """Return full records for a set of content hashes.

    Tombstoned records are intentionally excluded — they carry no value
    to a peer. Unknown hashes are silently omitted so clients can ask
    optimistically without handling per-hash 404s.

    **Request**

    ```json
    { "hashes": ["z...", "z...", ...] }   # max 100 per call
    ```

    **Response**

    ```json
    {
      "requested":  5,
      "found":      3,
      "records":    [MemoryResponse, ...]
    }
    ```
    """
    raw_hashes = payload.get("hashes") if isinstance(payload, dict) else None
    if not isinstance(raw_hashes, list) or not all(isinstance(h, str) for h in raw_hashes):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "message": "body must be an object with a 'hashes' array of strings",
                "next_steps": ['POST {"hashes": ["z...", ...]} with content-type: application/json'],
            },
        )
    service = SyncService(session=session, redis=redis)
    try:
        records = await service.fetch_records(raw_hashes)
    except TooManyHashesRequested as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "too_many_hashes",
                "message": str(exc),
                "next_steps": ["split the request into batches of ≤100 hashes"],
            },
        ) from exc

    logger.info(
        "sync_fetch_served",
        peer_did=peer.did,
        requested=len(raw_hashes),
        found=len(records),
    )
    return {
        "requested": len(raw_hashes),
        "found": len(records),
        "records": [MemoryResponse.model_validate(r).model_dump(by_alias=True) for r in records],
    }
