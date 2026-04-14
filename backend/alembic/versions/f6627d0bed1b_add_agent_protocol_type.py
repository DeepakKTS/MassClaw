"""add_agent_protocol_type

Revision ID: f6627d0bed1b
Revises: 1835c0436189
Create Date: 2026-04-14 16:01:44.173796

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6627d0bed1b'
down_revision: Union[str, Sequence[str], None] = '1835c0436189'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agents", sa.Column("protocol_type", sa.String(20), server_default="http", nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("agents", "protocol_type")
