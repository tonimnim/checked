"""Add in-person result confirmation columns to pairings

Revision ID: 0001
Revises:
Create Date: 2026-02-05

This migration adds columns for the in-person tournament result
claim/confirmation system where:
1. A player claims the result
2. Opponent receives push notification
3. Opponent confirms or disputes within 10 minutes
4. Disputed results escalate to admin/arbiter
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility (ALTER TABLE limitations)
    with op.batch_alter_table('pairings', schema=None) as batch_op:
        # Result claim tracking
        batch_op.add_column(sa.Column('claimed_result', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('claimed_by', sa.String(36), nullable=True))
        batch_op.add_column(sa.Column('claimed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('confirmation_deadline', sa.DateTime(), nullable=True))

        # Result confirmation tracking
        batch_op.add_column(sa.Column('confirmed_by', sa.String(36), nullable=True))
        batch_op.add_column(sa.Column('confirmed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('is_disputed', sa.Boolean(), nullable=True, server_default='0'))
        batch_op.add_column(sa.Column('dispute_reason', sa.Text(), nullable=True))

    # Create index for pending confirmations query
    op.create_index(
        'ix_pairings_claimed',
        'pairings',
        ['tournament_id', 'claimed_result', 'is_disputed'],
        unique=False
    )


def downgrade() -> None:
    # Remove index first
    op.drop_index('ix_pairings_claimed', table_name='pairings')

    # Remove columns using batch mode for SQLite
    with op.batch_alter_table('pairings', schema=None) as batch_op:
        batch_op.drop_column('dispute_reason')
        batch_op.drop_column('is_disputed')
        batch_op.drop_column('confirmed_at')
        batch_op.drop_column('confirmed_by')
        batch_op.drop_column('confirmation_deadline')
        batch_op.drop_column('claimed_at')
        batch_op.drop_column('claimed_by')
        batch_op.drop_column('claimed_result')
