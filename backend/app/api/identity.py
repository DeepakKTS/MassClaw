"""HTTP surface for DID resolution.

Exposes the DID resolver so peer MassClaw nodes, the frontend dashboard, and
any external agent can look up an AgentFacts document by its ``did:nanda:...``
handle without implementing the resolution logic themselves.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.identity.did import MalformedDIDError
from app.identity.did_resolver import ResolutionError
from app.services.identity_service import IdentityService

logger = get_logger(__name__)

router = APIRouter()


@router.get(
    "/resolve",
    tags=["Identity"],
    summary="Resolve a DID to a verified AgentFacts document.",
)
async def resolve_did(
    did: str = Query(..., description="DID to resolve, e.g. did:nanda:z6Mk..."),
    hint: str | None = Query(
        default=None,
        description="Optional well-known URL hint when the Index does not know the DID.",
        alias="well_known_url_hint",
    ),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Resolve a DID and return the AgentFacts document plus the source used.

    Sources are one of ``local``, ``nanda_index``, ``well_known``, or ``cache``.
    A 404 is returned when no strategy yields a result; 400 for malformed DIDs.
    """
    service = IdentityService(session=session, redis=redis)
    try:
        resolved = await service.resolve_did(did, well_known_url_hint=hint)
    except MalformedDIDError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "malformed_did",
                "message": str(exc),
                "next_steps": [
                    "Ensure the DID follows did:<method>:<identifier> format.",
                    "For MassClaw, the method is 'nanda' and the identifier is a multibase-encoded public key (prefix 'z').",
                ],
            },
        ) from exc
    except ResolutionError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "did_not_resolvable",
                "message": str(exc),
                "next_steps": [
                    "Confirm the DID is registered on this MassClaw node, the NANDA Index, or accessible at a well-known URL.",
                    "If you know the facts URL, pass it via the 'hint' query parameter.",
                ],
            },
        ) from exc

    return {
        "did": resolved.did,
        "source": resolved.source,
        "agent_facts": resolved.agent_facts.to_document(),
    }


@router.post(
    "/invalidate-cache",
    tags=["Identity"],
    summary="Invalidate a cached DID resolution.",
)
async def invalidate_resolution_cache(
    did: str = Query(..., description="DID whose cached resolution should be dropped."),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, str]:
    """Drop a cached DID resolution — useful after an agent rotates its key."""
    service = IdentityService(session=session, redis=redis)
    try:
        await service.invalidate_resolution(did)
    except MalformedDIDError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"did": did, "status": "invalidated"}
