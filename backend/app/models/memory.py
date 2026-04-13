from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import pg_enum
from app.models.base import Base, MemoryType

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.workflow import Workflow


class MemoryRecord(Base):
    __tablename__ = "memory_records"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    memory_type: Mapped[MemoryType] = mapped_column(
        pg_enum(MemoryType), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(384), nullable=True
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.8")
    )
    freshness: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("1.0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_records.memory_id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    )

    def __repr__(self) -> str:
        return f"<MemoryRecord(type={self.memory_type}, confidence={self.confidence:.2f}, v={self.version})>"
