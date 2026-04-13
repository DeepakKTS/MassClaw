from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import pg_enum
from app.models.base import AuditMixin, Base, PolicyAction, PolicyRuleType


class PolicyRule(Base, AuditMixin):
    __tablename__ = "policy_rules"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[PolicyRuleType] = mapped_column(
        pg_enum(PolicyRuleType), nullable=False, index=True
    )
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[PolicyAction] = mapped_column(pg_enum(PolicyAction), nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    def __repr__(self) -> str:
        return f"<PolicyRule(name={self.name!r}, action={self.action}, priority={self.priority})>"
