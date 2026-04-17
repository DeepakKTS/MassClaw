from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import pg_enum
from app.models.base import Base, MemoryType, RecordState

if TYPE_CHECKING:
    from app.models.workflow import Workflow


class MemoryRecord(Base):
    __tablename__ = "memory_records"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Nullable: the semantic cache writes cross-workflow entries with
    # workflow_id=None so a cache hit can serve any future workflow.
    # Workflow-scoped records (task outputs, checkpoints, audit) still
    # set workflow_id explicitly, and CASCADE still cleans them when
    # their owning workflow is deleted.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    memory_type: Mapped[MemoryType] = mapped_column(pg_enum(MemoryType), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.8"))
    freshness: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_records.memory_id", ondelete="SET NULL"),
        nullable=True,
    )
    # ---------- CRDT / provenance fields (added in v1.1.0) ----------
    # Author DID — the agent/instance that produced this record.
    # Nullable so rows written before the migration (legacy unsigned records)
    # remain readable. New writes should always supply this.
    author_did: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        index=True,
    )
    # Provenance chain: content hashes of the records this one builds on.
    # Empty list for a fresh fact; multi-entry when a record merges two
    # or more predecessors. Stored as a TEXT[] so ANY() queries work for
    # "find all records that cite hash X as a parent".
    parent_hashes: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)),
        nullable=False,
        server_default=text("ARRAY[]::varchar[]"),
    )
    # Ed25519 signature over the canonical bytes of the signable body,
    # multibase-encoded (``z`` + base58btc). Nullable for legacy records.
    signature: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    # Content hash — the record's address in the CRDT store. Uniquely
    # identifies the (immutable) record; used for content-addressed fetch
    # across nodes. Nullable for legacy records; unique when present.
    content_hash: Mapped[str | None] = mapped_column(
        "hash",
        String(128),
        nullable=True,
        unique=True,
        index=True,
    )
    # Lifecycle state — drives GC and read filtering.
    record_state: Mapped[RecordState] = mapped_column(
        pg_enum(RecordState),
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # Relationships
    workflow: Mapped[Workflow] = relationship(back_populates="memory_records")
    parent_version: Mapped[MemoryRecord | None] = relationship(
        remote_side=[memory_id],
        foreign_keys=[parent_version_id],
    )

    __table_args__ = (
        # IVFFlat index for approximate nearest neighbor search
        Index(
            "ix_memory_records_embedding_ivfflat",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # GIN index on parent_hashes so "find children of hash X" is fast:
        # SELECT ... WHERE :h = ANY(parent_hashes)  →  index scan.
        Index(
            "ix_memory_records_parent_hashes_gin",
            "parent_hashes",
            postgresql_using="gin",
        ),
        # Composite index for read queries: active records per workflow
        # scanned newest-first.
        Index(
            "ix_memory_records_workflow_state_created",
            "workflow_id",
            "record_state",
            "created_at",
        ),
    )

    @property
    def is_signed(self) -> bool:
        """Return True when this record has both a content hash and a signature."""
        return self.content_hash is not None and self.signature is not None

    def __repr__(self) -> str:
        return (
            f"<MemoryRecord(type={self.memory_type}, state={self.record_state}, "
            f"confidence={self.confidence:.2f}, v={self.version}, "
            f"hash={self.content_hash[:8] + '...' if self.content_hash else 'unsigned'})>"
        )
