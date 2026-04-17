from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.dependencies import get_memory_service
from app.models.base import MemoryType
from app.models.memory import MemoryRecord
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.memory import (
    FactResolutionRequest,
    FactResolutionResponse,
    MemoryQueryRequest,
    MemoryResponse,
    MemorySearchResult,
    MemoryWriteRequest,
    TombstoneRequest,
)
from app.services.memory_lifecycle import TombstoneAuthError
from app.services.memory_service import MemoryService

router = APIRouter()


@router.post("/write", response_model=MemoryResponse, status_code=201)
async def write_memory(
    data: MemoryWriteRequest,
    service: MemoryService = Depends(get_memory_service),
) -> MemoryResponse:
    """Write a memory record with automatic embedding generation.

    Handles version chains (via parent_version_id) and
    conflict resolution (last-writer-wins weighted by confidence).
    """
    record = await service.write_memory(data)
    return MemoryResponse.model_validate(record)


@router.post("/query", response_model=list[MemorySearchResult])
async def query_memory(
    query: MemoryQueryRequest,
    service: MemoryService = Depends(get_memory_service),
) -> list[MemorySearchResult]:
    """Semantic search over memory records using vector similarity.

    Results are ranked by: relevance_score = similarity * confidence * freshness_factor
    where freshness_factor = exp(-lambda * hours_since_creation).
    """
    return await service.query_memory(query)


@router.post(
    "/facts/resolve",
    response_model=FactResolutionResponse,
    summary="Resolve a fact across conflicting CRDT records under a chosen read mode.",
)
async def resolve_fact(
    request: FactResolutionRequest,
    service: MemoryService = Depends(get_memory_service),
) -> FactResolutionResponse:
    """Answer a question against the CRDT set with explicit mode semantics.

    The CRDT converges on a *set* of signed records; this endpoint turns
    that set into a single actionable outcome based on ``mode``:

    - ``planning`` — return one winner (``chosen``), ranked by
      ``confidence × freshness × author_trust``. For agents that need an
      answer to proceed.
    - ``audit`` — return every candidate with its rank and provenance.
      For dashboards, the policy engine, and human auditors. No winner
      is picked (``chosen`` is null).
    - ``sensitive`` — return no winner and set ``requires_hitl=true``
      when two candidates tie at high confidence. For actions whose
      mistake cannot be cheaply undone (payments, deletions, external
      messages). Callers should route the response into the HITL
      approvals queue.

    Every response includes the full ranked candidate list regardless
    of mode, so callers can surface the "evidence" without a second
    round-trip.
    """
    return await service.resolve_fact(request)


@router.get("/workflow/{workflow_id}", response_model=PaginatedResponse[MemoryResponse])
async def get_workflow_memories(
    workflow_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    memory_type: MemoryType | None = Query(default=None),
    service: MemoryService = Depends(get_memory_service),
) -> PaginatedResponse[MemoryResponse]:
    """Get all memory records for a workflow, paginated."""
    pagination = PaginationParams(page=page, page_size=page_size)
    return await service.get_workflow_memories(workflow_id, pagination, memory_type=memory_type)


@router.get("/{memory_id}/versions", response_model=list[MemoryResponse])
async def get_memory_versions(
    memory_id: uuid.UUID,
    service: MemoryService = Depends(get_memory_service),
) -> list[MemoryResponse]:
    """Get the full version chain for a memory record (newest to oldest)."""
    return await service.get_memory_versions(memory_id)


@router.delete("/{memory_id}", status_code=204, response_model=None)
async def delete_memory(
    memory_id: uuid.UUID,
    service: MemoryService = Depends(get_memory_service),
) -> None:
    """Soft-delete a memory record (sets confidence to 0, excluded from searches)."""
    await service.delete_memory(memory_id)


@router.post(
    "/tombstone",
    response_model=MemoryResponse,
    status_code=201,
    summary="Write a signed tombstone record and mark the target as TOMBSTONED.",
)
async def tombstone_memory_record(
    payload: TombstoneRequest,
    service: MemoryService = Depends(get_memory_service),
) -> MemoryResponse:
    """Federation-correct deletion path.

    The caller signs a tombstone record (a memory row with
    ``metadata.tombstone = true`` and ``parent_hashes = [target_hash]``)
    with their instance key. We verify the signature and that the
    ``author_did`` matches the target record's author, then persist the
    tombstone and flip the target's ``record_state`` to ``TOMBSTONED``.

    Peers learn about the tombstone through normal CRDT sync — the
    tombstone record itself shows up in their Merkle summary and they
    apply the state flip locally when they fetch it.

    **Returns**: the persisted tombstone record.

    **Status codes**:
    - ``201`` — tombstone accepted.
    - ``403`` — ``author_did`` does not match the target record's author,
      or the target is unsigned / already tombstoned
      (``{error: "tombstone_not_allowed", ...}``).
    - ``404`` — no record with the given ``target_hash``.
    - ``400`` — signature or hash doesn't verify against the canonical body.
    """
    try:
        record = await service.tombstone_memory(
            target_hash=payload.target_hash,
            requesting_did=payload.author_did,
            signature=payload.signature,
            content_hash=payload.content_hash,
            reason=payload.reason,
        )
    except TombstoneAuthError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tombstone_not_allowed",
                "message": str(exc),
                "next_steps": [
                    "Ensure the author_did in your tombstone request matches the target record's author_did.",
                    "Unsigned legacy records cannot be tombstoned — use the admin GC worker instead.",
                ],
            },
        ) from exc
    return MemoryResponse.model_validate(record)


@router.get(
    "/by-hash/{content_hash}",
    response_model=MemoryResponse,
    summary="Fetch a memory record by its content hash.",
)
async def get_memory_by_hash(
    content_hash: str,
    session: AsyncSession = Depends(get_db_session),
) -> MemoryResponse:
    """Content-addressed retrieval of a signed memory record.

    This is how peer MassClaw nodes fetch records by hash during CRDT sync,
    and how external agents verify a record referenced by its hash alone.
    The endpoint returns 404 for both "unknown hash" and "the hash exists
    but the record has been tombstoned" — callers must not distinguish
    between the two (tombstoned records are eligible for garbage collection
    and must not be served).
    """
    if not content_hash or len(content_hash) > 128:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_hash",
                "message": "content hash must be 1..128 characters",
                "next_steps": [
                    "Pass the multibase-encoded hash of the record's signable body.",
                ],
            },
        )
    result = await session.execute(select(MemoryRecord).where(MemoryRecord.content_hash == content_hash))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"no memory record with hash {content_hash!r}",
                "next_steps": [
                    "Confirm the hash is multibase-encoded (starts with 'z').",
                    "The record may have been tombstoned — tombstoned records are not served.",
                ],
            },
        )
    from app.models.base import RecordState

    if record.record_state == RecordState.TOMBSTONED:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "memory_not_found",
                "message": f"record {content_hash!r} has been tombstoned",
                "next_steps": [
                    "Tombstoned records are not served; they exist only to propagate deletion.",
                ],
            },
        )
    return MemoryResponse.model_validate(record)
