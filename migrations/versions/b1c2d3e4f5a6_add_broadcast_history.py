"""add broadcast history

Revision ID: b1c2d3e4f5a6
Revises: 4a047d892894
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = '4a047d892894'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'broadcast_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('total_recipients', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('attempted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_summary', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_broadcast_history_id'), 'broadcast_history', ['id'], unique=False)
    op.create_index(op.f('ix_broadcast_history_channel'), 'broadcast_history', ['channel'], unique=False)
    op.create_index(op.f('ix_broadcast_history_created_at'), 'broadcast_history', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_broadcast_history_created_at'), table_name='broadcast_history')
    op.drop_index(op.f('ix_broadcast_history_channel'), table_name='broadcast_history')
    op.drop_index(op.f('ix_broadcast_history_id'), table_name='broadcast_history')
    op.drop_table('broadcast_history')
