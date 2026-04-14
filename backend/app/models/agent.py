from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import pg_enum
from app.models.base import AgentStatus, AuditMixin, Base

# Import TYPE_CHECKING to avoid circular imports
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.score import AgentScore
    from app.models.task import Task
    from app.models.trust import TrustEvent
    from app.models.wallet import WalletEvent


class Agent(Base, AuditMixin):
    __tablename__ = "agents"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    supported_tools: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    cost_profile: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    latency_profile: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    trust_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=text("0.5"),
    )
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'1.0.0'"),
    )
    safety_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    input_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[AgentStatus] = mapped_column(
        pg_enum(AgentStatus),
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )
    health_check_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    last_health_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    # Relationships
    tasks: Mapped[list[Task]] = relationship(
        back_populates="agent", lazy="selectin"
    )
    trust_events: Mapped[list[TrustEvent]] = relationship(
        back_populates="agent",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    scores: Mapped[list[AgentScore]] = relationship(
        back_populates="agent",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # GIN index on capabilities for @> containment queries
        Index("ix_agents_capabilities_gin", "capabilities", postgresql_using="gin"),
        # Composite index for ranked discovery
        Index(
            "ix_agents_status_trust",
            "status",
            trust_score.desc(),
        ),
        # GIN trigram index on name for fuzzy search
        Index(
            "ix_agents_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Agent(name={self.name!r}, status={self.status}, trust={self.trust_score:.2f})>"
