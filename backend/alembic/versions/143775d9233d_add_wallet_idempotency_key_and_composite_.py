"""Add wallet idempotency key and composite index

Revision ID: 143775d9233d
Revises: de32d0b64c0a
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '143775d9233d'
down_revision: Union[str, Sequence[str], None] = 'de32d0b64c0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add idempotency_key column and composite index to wallet_events."""
    with op.batch_alter_table('wallet_events', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('idempotency_key', sa.String(length=255), nullable=True)
        )
        batch_op.create_index(
            batch_op.f('ix_wallet_events_idempotency_key'),
            ['idempotency_key'],
            unique=True,
        )
        batch_op.create_index(
            'ix_wallet_events_workflow_action',
            ['workflow_id', 'action_type'],
            unique=False,
        )


def downgrade() -> None:
    """Remove idempotency_key column and composite index from wallet_events."""
    with op.batch_alter_table('wallet_events', schema=None) as batch_op:
        batch_op.drop_index('ix_wallet_events_workflow_action')
        batch_op.drop_index(batch_op.f('ix_wallet_events_idempotency_key'))
        batch_op.drop_column('idempotency_key')
