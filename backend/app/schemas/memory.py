from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import MemoryType


class MemoryWriteRequest(BaseModel):
    """Request to write a memory record."""

    workflow_id: uuid.UUID
    source_agent_id: uuid.UUID | None = None
    memory_type: MemoryType = MemoryType.RESULT
    content: str = Field(..., min_length=1, max_length=100000)
    confidence: float = Field(default=0.8, ge=0, le=1)
    metadata: dict = Field(default_factory=dict)
    parent_version_id: uuid.UUID | None = None
    ttl_hours: int | None = Field(default=None, ge=1, description="Time-to-live in hours")


class MemoryQueryRequest(BaseModel):
    """Request for semantic memory search."""

    query: str = Field(..., min_length=1, max_length=10000)
    workflow_id: uuid.UUID | None = None
    memory_types: list[MemoryType] | None = None
    min_similarity: float = Field(default=0.5, ge=0, le=1)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    top_k: int = Field(default=10, ge=1, le=100)


class MemoryResponse(BaseModel):
    """Full memory record response."""

    model_config = ConfigDict(from_attributes=True)

    memory_id: uuid.UUID
    workflow_id: uuid.UUID
    source_agent_id: uuid.UUID | None
    memory_type: MemoryType
    content: str
    metadata: dict = Field(alias="metadata_")
    confidence: float
    freshness: float
    version: int
    parent_version_id: uuid.UUID | None
    expires_at: datetime | None
    created_at: datetime


class MemorySearchResult(BaseModel):
    """Memory record with similarity score from search."""

    memory: MemoryResponse
    similarity: float
    relevance_score: float  # similarity * confidence * freshness
