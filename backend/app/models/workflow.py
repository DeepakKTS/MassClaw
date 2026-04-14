from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import pg_enum
from app.models.base import AuditMixin, Base, WorkflowStatus

if TYPE_CHECKING:
    from app.models.memory import MemoryRecord
    from app.models.task import Task
    from app.models.wallet import WalletEvent


class Workflow(Base, AuditMixin):
    __tablename__ = "workflows"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[WorkflowStatus] = mapped_column(
        pg_enum(WorkflowStatus),
        nullable=False,
        server_default=text("'pending'"),
        index=True,
    )
    budget_limit: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    budget_used: Mapped[float] = mapped_column(
        Numeric(12, 4),
        nullable=False,
        server_default=text("0"),
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    dag_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    execution_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    goal_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reasoning_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tasks: Mapped[list[Task]] = relationship(
        back_populates="workflow",
        lazy="select",
        cascade="all, delete-orphan",
    )
    memory_records: Mapped[list[MemoryRecord]] = relationship(
        back_populates="workflow",
        lazy="select",
        cascade="all, delete-orphan",
    )
    wallet_events: Mapped[list[WalletEvent]] = relationship(
        back_populates="workflow",
        lazy="select",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Workflow(id={self.workflow_id}, status={self.status}, domain={self.domain!r})>"
