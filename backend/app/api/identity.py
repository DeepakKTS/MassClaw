"""HTTP surface for DID resolution.

Exposes the DID resolver so peer MassClaw nodes, the frontend dashboard, and
any external agent can look up an AgentFacts document by its DID without
implementing the resolution logic themselves.

DIDs accepted, in order of preference:

- ``did:key:z<multibase-Ed25519-pub>`` — W3C-standard, self-contained
  (identifier carries the public key). Preferred for new integrations.
- ``did:web:<host>[:path]`` — authenticity rooted in a DNS domain; the
  resolver verifies by matching ``provider.did``.
- ``did:nanda:z<multibase>`` — legacy MassClaw method, still accepted for
  backward compatibility.
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
    did: str = Query(
        ...,
        description="DID to resolve. Canonical: did:key:z<multibase-Ed25519-pub>. "
        "Also accepted: did:web:<host>, did:nanda:z<multibase> (legacy).",
        examples=["did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"],
    ),
    username_hint: str | None = Query(
        default=None,
        description="Optional NANDA Index username to use for lookup when the "
        "DID is not registered locally. The real NANDA Index resolves by "
        "username, not by DID, so this hint is required for Index fallback.",
    ),
    hint: str | None = Query(
        default=None,
        description="Optional https:// URL pointing at the agent's "
        "/.well-known/agent-facts.json — last-resort resolution strategy.",
        alias="well_known_url_hint",
    ),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Resolve a DID and return the AgentFacts v1 document plus the source used.

    **Resolution order**: local registry → NANDA Index (when ``username_hint``
    is provided) → well-known URL (when ``well_known_url_hint`` is provided).

    **Response**:

    ```json
    {
      "did": "did:key:z6Mk...",
      "source": "local",
      "agent_facts": { ...NANDA AgentFacts v1 document... }
    }
    ```

    **Sources**: ``local`` | ``nanda_index`` | ``well_known`` | ``cache``.

    **Status codes**:
    - ``200`` — resolved and signature/provider DID verified.
    - ``400`` — DID is malformed (``{error: "malformed_did", ...}``).
    - ``404`` — no strategy returned a document (``{error: "did_not_resolvable", ...}``).
    """
    service = IdentityService(session=session, redis=redis)
    try:
        resolved = await service.resolve_did(
            did,
            username_hint=username_hint,
            well_known_url_hint=hint,
        )
    except MalformedDIDError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "malformed_did",
                "message": str(exc),
                "next_steps": [
                    "Use the canonical form did:key:z<multibase-Ed25519-pub>.",
                    "did:web:<host> and did:nanda:z<multibase> (legacy) are also accepted.",
                    "The identifier after the method name must be non-empty.",
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
                    "Pass 'username_hint' if the agent is registered on the NANDA Index.",
                    "Pass 'well_known_url_hint' (https://.../.well-known/agent-facts.json) if you know where the document is hosted.",
                    "Verify that the DID's provider actually serves an AgentFacts document.",
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
    """Drop a cached DID resolution — useful after an agent rotates its key.

    Returns ``{did, status: "invalidated"}`` on success. Always succeeds when
    the DID is well-formed; cache misses are silent.
    """
    service = IdentityService(session=session, redis=redis)
    try:
        await service.invalidate_resolution(did)
    except MalformedDIDError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "malformed_did",
                "message": str(exc),
                "next_steps": [
                    "Pass a well-formed DID (did:key:, did:web:, or legacy did:nanda:).",
                ],
            },
        ) from exc
    return {"did": did, "status": "invalidated"}
