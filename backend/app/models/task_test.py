from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models.task import Task


class TaskTest(Base, AuditMixin):
    __tablename__ = "task_tests"

    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    test_type: Mapped[str] = mapped_column(
        String(50), nullable=False,  # "assertion", "llm_eval", "regex", "schema"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False,  # "pass", "fail", "error", "skipped"
    )
    assertion: Mapped[dict] = mapped_column(
        JSONB, nullable=False,  # {"type": "contains", "expected": "...", ...}
    )
    actual_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    def __repr__(self) -> str:
        return f"<TaskTest(name={self.test_name!r}, status={self.status})>"
