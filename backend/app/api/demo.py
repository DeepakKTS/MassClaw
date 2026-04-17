"""Demo-only HTTP endpoints used by the federation docker-compose.

These endpoints exist for the three-node convergence demo (see
``docker/federation.yml``). Every route is gated behind
:func:`_require_demo_mode`, which 404s when ``MASSCLAW_DEMO_MODE`` is
falsy — in production, this entire router appears to not exist.

What lives here:

- ``POST /demo/seed-workflow`` — create (or return the existing) workflow
  row with a caller-provided UUID. Makes it trivial for a shell script
  to create the **same** workflow on all three nodes so the gossiped
  memory records have a legitimate foreign key to resolve against.
- ``POST /demo/self-sign-write`` — write a signed memory record using the
  node's own instance Ed25519 key as the author. Indispensable for the
  demo scripts (which are plain bash) because signing Ed25519 over
  RFC-8785-canonical JSON inside a shell one-liner is not something we
  want to ask an operator to do.

Both endpoints return the canonical ``{error, message, next_steps}``
envelope on failure so stock CLIs can recover without reading source.
"""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.crdt.store import CRDTStore
from app.identity.did import build_did_key
from app.models.base import MemoryType, RecordState
from app.models.workflow import Workflow
from app.schemas.memory import MemoryResponse
from app.services.identity_service import get_instance_key_store

logger = get_logger(__name__)

router = APIRouter()


def _require_demo_mode() -> None:
    settings = get_settings()
    if not settings.massclaw_demo_mode:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "demo_endpoints_disabled",
                "message": "demo endpoints require MASSCLAW_DEMO_MODE=true",
                "next_steps": [
                    "these endpoints are gated for local federation demos only.",
                    "they are intentionally absent from production builds.",
                ],
            },
        )


# ---------------------------------------------------------------- seed workflow


@router.post(
    "/seed-workflow",
    summary="Create (or return) a workflow with a caller-supplied UUID.",
)
async def seed_workflow(
    payload: dict[str, Any] = Body(
        ...,
        description='Body: {"workflow_id": "00000000-0000-4000-8000-000000000001", "prompt": "demo", "user_id": "demo-user"}',
    ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Idempotently create a workflow row with the given UUID.

    Used by the federation demo so all three nodes share the same
    ``workflow_id`` — without this, a gossip-pulled record's FK to
    ``workflows.workflow_id`` has nothing to resolve against and the
    record is silently skipped.
    """
    _require_demo_mode()

    try:
        workflow_uuid = uuid.UUID(str(payload.get("workflow_id")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_workflow_id",
                "message": f"workflow_id must be a UUID string: {exc}",
                "next_steps": ["Supply a 36-char UUID in the workflow_id field."],
            },
        ) from exc

    existing = await session.execute(select(Workflow).where(Workflow.workflow_id == workflow_uuid))
    wf = existing.scalar_one_or_none()
    if wf is not None:
        return {
            "workflow_id": str(wf.workflow_id),
            "created": False,
            "status": wf.status.value,
        }

    prompt = str(payload.get("prompt") or "federation demo workflow")
    user_id = str(payload.get("user_id") or "federation-demo")
    wf = Workflow(
        workflow_id=workflow_uuid,
        user_id=user_id,
        prompt=prompt,
        domain="federation-demo",
        budget_limit=500,
    )
    session.add(wf)
    await session.flush()
    logger.info(
        "demo_workflow_seeded",
        workflow_id=str(workflow_uuid),
        user_id=user_id,
    )
    return {
        "workflow_id": str(wf.workflow_id),
        "created": True,
        "status": wf.status.value,
    }


# ---------------------------------------------------------------- self-sign write


@router.post(
    "/self-sign-write",
    response_model=MemoryResponse,
    status_code=201,
    summary="Write a signed memory record using the node's own instance key.",
)
async def self_sign_write(
    payload: dict[str, Any] = Body(
        ...,
        description='Body: {"workflow_id": "...", "content": "...", "confidence": 0.9, "metadata": {...}, "parent_hashes": []}',
    ),
    session: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> MemoryResponse:
    """Sign and persist a memory record using the node's instance keypair.

    The demo scripts call this endpoint rather than the regular
    ``/memory/write`` because:
    1. Signing Ed25519 in bash is awkward.
    2. The scripts want each node's records authored by that node's DID
       so peers can verify them through the normal gossip path.

    This endpoint is disabled in production (``MASSCLAW_DEMO_MODE=false``).
    """
    _require_demo_mode()

    try:
        workflow_uuid = uuid.UUID(str(payload.get("workflow_id")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_workflow_id",
                "message": f"workflow_id must be a UUID string: {exc}",
                "next_steps": ["Call /demo/seed-workflow first if the workflow doesn't exist locally."],
            },
        ) from exc

    content = payload.get("content")
    if not isinstance(content, str) or not content:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_content",
                "message": "content must be a non-empty string",
                "next_steps": ["Include a 'content' field in the body."],
            },
        )

    confidence_raw = payload.get("confidence", 0.9)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.9
    confidence = max(0.0, min(1.0, confidence))

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    parent_hashes_raw = payload.get("parent_hashes") or []
    if not isinstance(parent_hashes_raw, list):
        parent_hashes_raw = []
    parent_hashes: list[str] = [str(h) for h in parent_hashes_raw if isinstance(h, str)]

    memory_type_raw = payload.get("memory_type", "result")
    try:
        memory_type = MemoryType(str(memory_type_raw))
    except ValueError:
        memory_type = MemoryType.RESULT

    keypair = get_instance_key_store().instance_keypair()
    author_did = build_did_key(keypair.public_bytes)

    store = CRDTStore(session=session)
    record = await store.put(
        workflow_id=workflow_uuid,
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        metadata=metadata,
        parent_hashes=parent_hashes,
        author_did=author_did,
        keypair=keypair,
        record_state=RecordState.ACTIVE,
    )

    # Drop any cached Merkle summary for the workflow + instance so the
    # next gossip tick re-reads the fresh data.
    try:
        await redis.delete(
            "crdt:summary:all",
            f"crdt:summary:wf-{workflow_uuid}",
        )
    except Exception as exc:
        logger.warning("demo_summary_invalidate_failed", error=str(exc))

    logger.info(
        "demo_self_sign_write",
        workflow_id=str(workflow_uuid),
        author_did=author_did,
        content_hash=(record.content_hash or "")[:12],
    )
    return MemoryResponse.model_validate(record)
