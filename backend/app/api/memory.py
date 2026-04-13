from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_memory_service
from app.models.base import MemoryType
from app.schemas.common import PaginatedResponse, PaginationParams
from app.schemas.memory import (
    MemoryQueryRequest,
    MemoryResponse,
    MemorySearchResult,
    MemoryWriteRequest,
)
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
    return await service.get_workflow_memories(
        workflow_id, pagination, memory_type=memory_type
    )


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
