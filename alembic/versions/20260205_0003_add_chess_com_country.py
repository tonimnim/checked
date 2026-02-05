"""Add chess_com_country column to players

Revision ID: 20260205_0003
Revises: 20260205_0002
Create Date: 2026-02-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260205_0003'
down_revision: Union[str, None] = '20260205_0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add chess_com_country column to players table
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.add_column(sa.Column('chess_com_country', sa.String(5), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.drop_column('chess_com_country')
