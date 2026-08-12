from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AuditMixin, Base

if TYPE_CHECKING:
    pass


class Project(Base, AuditMixin):
    __tablename__ = "projects"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("'default'"))
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    backlog_tasks: Mapped[list[BacklogTask]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sprints: Mapped[list[Sprint]] = relationship(back_populates="project", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Project(name={self.name!r}, status={self.status})>"


class BacklogTask(Base, AuditMixin):
    __tablename__ = "backlog_tasks"

    backlog_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    story_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'new'"),
        index=True,
    )

    project: Mapped[Project] = relationship(back_populates="backlog_tasks")
    sprint_tasks: Mapped[list[SprintTask]] = relationship(back_populates="backlog_task")

    def __repr__(self) -> str:
        return f"<BacklogTask(title={self.title!r}, priority={self.priority})>"


class Sprint(Base, AuditMixin):
    __tablename__ = "sprints"

    sprint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'planning'"),
        index=True,
    )

    project: Mapped[Project] = relationship(back_populates="sprints")
    sprint_tasks: Mapped[list[SprintTask]] = relationship(back_populates="sprint", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Sprint(name={self.name!r}, status={self.status})>"


class SprintTask(Base, AuditMixin):
    __tablename__ = "sprint_tasks"

    sprint_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    sprint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sprints.sprint_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    backlog_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backlog_tasks.backlog_task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'todo'"),
        index=True,
    )
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    sprint: Mapped[Sprint] = relationship(back_populates="sprint_tasks")
    backlog_task: Mapped[BacklogTask] = relationship(back_populates="sprint_tasks")

    def __repr__(self) -> str:
        return f"<SprintTask(status={self.status}, position={self.position})>"
