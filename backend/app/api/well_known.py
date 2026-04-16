"""Well-known HTTP routes served at the root of the MassClaw instance.

Currently exposes:

- ``/.well-known/agent-facts.json`` — the signed AgentFacts document that a
  stock OpenClaw agent fetches first to learn what this MassClaw node is and
  how to use it.

This router is mounted at the **root** (no prefix) so the URL matches the
``/.well-known/...`` convention on the public host.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.redis import get_redis
from app.services.identity_service import IdentityService

router = APIRouter()


@router.get(
    "/.well-known/agent-facts.json",
    tags=["Identity"],
    summary="Signed AgentFacts for this MassClaw instance.",
    response_model=None,
)
async def well_known_agent_facts(
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Return the signed NANDA AgentFacts v1 document for this MassClaw node.

    Stock discovery flow for an OpenClaw agent:

    1. `GET /.well-known/agent-facts.json` (this endpoint).
    2. Read top-level ``endpoints.static[]`` for the list of base URLs to call.
    3. Read ``capabilities.modalities`` and ``capabilities.authentication.methods``
       to decide what inputs to prepare and which auth header to send.
    4. Read ``skills[]`` — each entry has ``id``, ``description``, ``inputModes``,
       ``outputModes`` — to pick the skill that matches the task.
    5. Optionally read ``x-massclaw.example_requests`` for copy-pasteable bodies,
       and ``x-massclaw.error_schema`` for the structured-error contract.
    6. Verify the document by ``POST /api/v1/agents/verify-facts`` with the body
       returned here. The proof lives in ``verifiable_credentials[0].proof``
       (``DataIntegrityProof`` + ``eddsa-rdfc-2022`` cryptosuite).
    7. Call the matching endpoint (e.g. ``POST /api/v1/workflows/submit``).

    The returned JSON is the NANDA AgentFacts v1 schema
    (``https://agentfacts.org/schema/v1``) — flat object, no W3C VC envelope.
    """
    service = IdentityService(session=session, redis=redis)
    facts = await service.get_instance_facts()
    return facts.to_document()
