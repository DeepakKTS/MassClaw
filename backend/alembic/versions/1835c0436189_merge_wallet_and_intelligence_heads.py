"""merge_wallet_and_intelligence_heads

Revision ID: 1835c0436189
Revises: 143775d9233d, 6ae375ab1f61
Create Date: 2026-04-14 06:28:10.830471

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1835c0436189'
down_revision: Union[str, Sequence[str], None] = ('143775d9233d', '6ae375ab1f61')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
