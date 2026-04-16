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
    """Return the signed AgentFacts document for this MassClaw node.

    Stock discovery flow:
    1. Agent fetches ``/.well-known/agent-facts.json``.
    2. Reads ``credentialSubject.endpoints`` for the MCP + REST URLs.
    3. Reads ``credentialSubject.capabilities`` to decide what to try.
    4. Calls those endpoints and completes the task.
    """
    service = IdentityService(session=session, redis=redis)
    facts = await service.get_instance_facts()
    return facts.to_document()
