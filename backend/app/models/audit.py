from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import pg_enum
from app.models.base import ActorType, AuditEventType, Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.task_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        pg_enum(AuditEventType), nullable=False, index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        pg_enum(ActorType), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        # GIN trigram indexes for full-text search on summaries
        Index(
            "ix_audit_logs_input_summary_trgm",
            "input_summary",
            postgresql_using="gin",
            postgresql_ops={"input_summary": "gin_trgm_ops"},
        ),
        Index(
            "ix_audit_logs_output_summary_trgm",
            "output_summary",
            postgresql_using="gin",
            postgresql_ops={"output_summary": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(event={self.event_type}, actor={self.actor_id})>"
