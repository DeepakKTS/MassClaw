"""add cache value to memorytype enum

Revision ID: 9c1f4b7a2d38
Revises: 6e91698ee915
Create Date: 2026-08-12 18:10:00.000000

The semantic result cache used to write its entries as ``memory_type='result'``
with ``confidence=0.85`` — byte for byte what a genuine task output looks like
(``WorkflowScheduler`` writes real results at that exact confidence). Its read
filter matched on those two columns, so a "cache hit" could return any
workflow's real output to an unrelated caller.

``cache`` makes the entries their own class: cross-workflow by construction
(``workflow_id IS NULL``), expiring, and filterable. Rows written under the old
scheme keep ``memory_type='result'`` and are simply never served as cache hits
again, which also retires every pre-guard poisoned entry.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9c1f4b7a2d38"
down_revision: str | Sequence[str] | None = "6e91698ee915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add 'cache' to the memorytype enum (idempotent)."""
    # ALTER TYPE ADD VALUE cannot run inside a transaction block in older
    # Postgres versions. Use autocommit block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE memorytype ADD VALUE IF NOT EXISTS 'cache'")


def downgrade() -> None:
    """Cannot remove enum values safely in PostgreSQL; downgrade is a no-op."""
    # Any rows already written as 'cache' would become unreadable if the value
    # disappeared, and PostgreSQL offers no ALTER TYPE ... DROP VALUE. Leaving
    # the value in place is safe: nothing depends on its absence.
    pass
