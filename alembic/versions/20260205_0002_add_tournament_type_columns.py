"""Add tournament type columns (online vs in-person)

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-05

This migration adds columns to support both online and in-person (OTB) tournaments:
- is_online: Boolean to distinguish tournament type
- venue: Physical location for in-person tournaments
- result_confirmation_minutes: Time window for opponent to confirm results
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('tournaments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_online', sa.Boolean(), nullable=True, server_default='1'))
        batch_op.add_column(sa.Column('venue', sa.String(200), nullable=True))
        batch_op.add_column(sa.Column('result_confirmation_minutes', sa.Integer(), nullable=True, server_default='10'))


def downgrade() -> None:
    with op.batch_alter_table('tournaments', schema=None) as batch_op:
        batch_op.drop_column('result_confirmation_minutes')
        batch_op.drop_column('venue')
        batch_op.drop_column('is_online')
