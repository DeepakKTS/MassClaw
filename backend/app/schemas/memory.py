from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import MemoryType, RecordState


class MemoryWriteRequest(BaseModel):
    """Request to write a memory record.

    The CRDT / provenance fields (``author_did``, ``parent_hashes``,
    ``signature``, ``hash``) are optional on the HTTP surface — callers who
    just want to store a fact can leave them blank and MassClaw will treat
    the row as a legacy unsigned record. Federated writes produced by peer
    nodes supply all four so the record participates in the CRDT sync.
    """

    workflow_id: uuid.UUID
    source_agent_id: uuid.UUID | None = None
    memory_type: MemoryType = MemoryType.RESULT
    content: str = Field(..., min_length=1, max_length=100000)
    confidence: float = Field(default=0.8, ge=0, le=1)
    metadata: dict = Field(default_factory=dict)
    parent_version_id: uuid.UUID | None = None
    ttl_hours: int | None = Field(default=None, ge=1, description="Time-to-live in hours")
    # CRDT / provenance fields
    author_did: str | None = Field(
        default=None,
        max_length=512,
        description="DID of the author agent/instance. did:key: preferred.",
    )
    parent_hashes: list[str] = Field(
        default_factory=list,
        description="Content hashes of predecessor records this one builds on.",
    )
    signature: str | None = Field(
        default=None,
        max_length=256,
        description="Multibase Ed25519 signature over the canonical signable body.",
    )
    content_hash: str | None = Field(
        default=None,
        max_length=128,
        alias="hash",
        description="Content address of this record. Must be unique; duplicates return 409.",
    )


class MemoryQueryRequest(BaseModel):
    """Request for semantic memory search."""

    query: str = Field(..., min_length=1, max_length=10000)
    workflow_id: uuid.UUID | None = None
    memory_types: list[MemoryType] | None = None
    min_similarity: float = Field(default=0.5, ge=0, le=1)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    top_k: int = Field(default=10, ge=1, le=100)
    # CRDT read filters
    include_states: list[RecordState] = Field(
        default_factory=lambda: [RecordState.ACTIVE],
        description="Which lifecycle states to include. Defaults to active only.",
    )
    author_did: str | None = Field(
        default=None,
        max_length=512,
        description="Filter by author DID.",
    )


class MemoryResponse(BaseModel):
    """Full memory record response."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

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
    # CRDT / provenance fields
    author_did: str | None = None
    parent_hashes: list[str] = Field(default_factory=list)
    signature: str | None = None
    content_hash: str | None = Field(default=None, alias="hash")
    record_state: RecordState = RecordState.ACTIVE


class MemorySearchResult(BaseModel):
    """Memory record with similarity score from search."""

    memory: MemoryResponse
    similarity: float
    relevance_score: float  # similarity * confidence * freshness
