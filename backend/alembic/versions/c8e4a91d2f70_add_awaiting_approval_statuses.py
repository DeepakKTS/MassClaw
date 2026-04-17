"""add awaiting_approval enum value to taskstatus and workflowstatus

Revision ID: c8e4a91d2f70
Revises: b4d1a2c3e7f9
Create Date: 2026-04-17 06:00:00.000000

The Python enums ``TaskStatus`` and ``WorkflowStatus`` both declare an
``AWAITING_APPROVAL`` value but the initial migration never added it to the
underlying Postgres enums. Any attempt to flip a row to that state
previously raised ``InvalidTextRepresentationError``, which broke the entire
HITL (human-in-the-loop) pause/approve/resume flow end-to-end. Surfaced by
the OpenClaw harness s8 run.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c8e4a91d2f70'
down_revision: Union[str, Sequence[str], None] = 'b4d1a2c3e7f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add awaiting_approval to both enums (idempotent)."""
    # ALTER TYPE ADD VALUE cannot run inside a transaction block in older
    # Postgres versions. Use autocommit block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'awaiting_approval'")
        op.execute("ALTER TYPE workflowstatus ADD VALUE IF NOT EXISTS 'awaiting_approval'")


def downgrade() -> None:
    """Cannot remove enum values safely in PostgreSQL; downgrade is a no-op."""
    # PostgreSQL does not support removing individual enum values. Running
    # this downgrade is safe (does nothing) because no other schema objects
    # depend on the added value being absent.
    pass
