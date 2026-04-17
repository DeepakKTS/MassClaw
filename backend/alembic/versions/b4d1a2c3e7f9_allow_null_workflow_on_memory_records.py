"""Allow NULL workflow_id on memory_records for the cross-workflow semantic cache.

The semantic cache (scheduler.py) writes `MemoryRecord(workflow_id=None)` so
a cache hit can serve any future workflow. The original migration declared
the column NOT NULL which crashed direct-response + DAG pipeline workflows
with a PendingRollbackError. Making it nullable keeps CASCADE cleanup for
workflow-scoped records and lets the cache coexist.

Revision ID: b4d1a2c3e7f9
Revises: a3f2c91b4d70
Create Date: 2026-04-17 03:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4d1a2c3e7f9"
down_revision = "a3f2c91b4d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "memory_records",
        "workflow_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Drop NULL rows first — impossible to re-add NOT NULL otherwise.
    op.execute("DELETE FROM memory_records WHERE workflow_id IS NULL")
    op.alter_column(
        "memory_records",
        "workflow_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
