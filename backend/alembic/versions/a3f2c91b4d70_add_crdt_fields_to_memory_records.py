"""Add CRDT / provenance fields to memory_records.

Adds:
- ``author_did`` (nullable, indexed) — DID of the author agent/instance.
- ``parent_hashes`` (varchar[] NOT NULL DEFAULT ARRAY[]) — content-addressed
  predecessors for the provenance chain. GIN-indexed for "find children of
  hash X" queries.
- ``signature`` (nullable) — multibase Ed25519 signature over the signable body.
- ``hash`` (nullable, unique, indexed) — the record's content address.
- ``record_state`` (record_state enum NOT NULL DEFAULT 'active', indexed) —
  lifecycle bucket used for GC and read filtering.
- Composite index ``ix_memory_records_workflow_state_created`` for the
  canonical read query (active records per workflow, newest first).

Revision ID: a3f2c91b4d70
Revises: 143775d9233d
Create Date: 2026-04-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a3f2c91b4d70"
down_revision: str | Sequence[str] | None = "143775d9233d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RECORD_STATE_ENUM_NAME = "recordstate"


def upgrade() -> None:
    """Add CRDT fields to memory_records and their supporting indices."""
    # 1. Create the record_state enum type.
    record_state_enum = postgresql.ENUM(
        "active",
        "superseded",
        "historical",
        "tombstoned",
        name=RECORD_STATE_ENUM_NAME,
        create_type=True,
    )
    record_state_enum.create(op.get_bind(), checkfirst=True)

    # 2. Add the new columns.
    op.add_column(
        "memory_records",
        sa.Column("author_did", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "memory_records",
        sa.Column(
            "parent_hashes",
            sa.ARRAY(sa.String(length=128)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
    )
    op.add_column(
        "memory_records",
        sa.Column("signature", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "memory_records",
        sa.Column("hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "memory_records",
        sa.Column(
            "record_state",
            postgresql.ENUM(
                "active",
                "superseded",
                "historical",
                "tombstoned",
                name=RECORD_STATE_ENUM_NAME,
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )

    # 3. Backfill — existing rows become legacy/unsigned with record_state='active'.
    #    No-op here because column defaults already populate the correct values.

    # 4. Indices.
    op.create_index(
        "ix_memory_records_author_did",
        "memory_records",
        ["author_did"],
        unique=False,
    )
    op.create_index(
        "ix_memory_records_hash",
        "memory_records",
        ["hash"],
        unique=True,
    )
    op.create_index(
        "ix_memory_records_record_state",
        "memory_records",
        ["record_state"],
        unique=False,
    )
    op.create_index(
        "ix_memory_records_parent_hashes_gin",
        "memory_records",
        ["parent_hashes"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_memory_records_workflow_state_created",
        "memory_records",
        ["workflow_id", "record_state", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove CRDT fields and indices from memory_records."""
    op.drop_index("ix_memory_records_workflow_state_created", table_name="memory_records")
    op.drop_index("ix_memory_records_parent_hashes_gin", table_name="memory_records")
    op.drop_index("ix_memory_records_record_state", table_name="memory_records")
    op.drop_index("ix_memory_records_hash", table_name="memory_records")
    op.drop_index("ix_memory_records_author_did", table_name="memory_records")

    op.drop_column("memory_records", "record_state")
    op.drop_column("memory_records", "hash")
    op.drop_column("memory_records", "signature")
    op.drop_column("memory_records", "parent_hashes")
    op.drop_column("memory_records", "author_did")

    postgresql.ENUM(name=RECORD_STATE_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
