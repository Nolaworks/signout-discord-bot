"""add admin_action_log table

Revision ID: 79456f6cbdfa
Revises: 65ed2a99d791
Create Date: 2026-03-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79456f6cbdfa'
down_revision: Union[str, None] = '65ed2a99d791'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'admin_action_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('admin_user_id', sa.String(50), nullable=False, index=True),
        sa.Column('admin_username', sa.String(100), nullable=False),
        sa.Column('action_type', sa.String(50), nullable=False, index=True),
        sa.Column('target_user_id', sa.String(50), nullable=True),
        sa.Column('target_username', sa.String(100), nullable=True),
        sa.Column('tool_name', sa.String(100), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
    )
    op.create_index('idx_admin_action_type_time', 'admin_action_log', ['action_type', 'created_at'])


def downgrade() -> None:
    op.drop_index('idx_admin_action_type_time', table_name='admin_action_log')
    op.drop_table('admin_action_log')
