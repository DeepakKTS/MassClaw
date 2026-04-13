from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import pg_enum
from app.models.base import Base, WalletActionType

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.workflow import Workflow


class WalletEvent(Base):
    __tablename__ = "wallet_events"

    wallet_event_id: Mapped[uuid.UUID] = mapped_column(
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
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[WalletActionType] = mapped_column(
        pg_enum(WalletActionType), nullable=False, index=True
    )
    credit_delta: Mapped[float] = mapped_column(
        Numeric(12, 4), nullable=False
    )
    balance_after: Mapped[float] = mapped_column(
        Numeric(12, 4), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    # Relationships
    workflow: Mapped[Workflow] = relationship(back_populates="wallet_events")

    def __repr__(self) -> str:
        return f"<WalletEvent(type={self.action_type}, delta={self.credit_delta})>"
