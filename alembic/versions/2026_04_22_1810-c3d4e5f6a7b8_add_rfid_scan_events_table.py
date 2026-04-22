"""add rfid_scan_events table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-22 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rfid_scan_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('card_id', sa.String(20), nullable=False),
        sa.Column('scanned_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('consumed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('consumed_by_admin_user_id', sa.String(50), nullable=True),
        sa.Column('consumed_by_admin_username', sa.String(100), nullable=True),
        sa.Column('assigned_user_id', sa.String(50), nullable=True),
        sa.Column('assigned_username', sa.String(100), nullable=True),
    )
    op.create_index('ix_rfid_scan_events_card_id', 'rfid_scan_events', ['card_id'])
    op.create_index('ix_rfid_scan_events_scanned_at', 'rfid_scan_events', ['scanned_at'])
    op.create_index('ix_rfid_scan_events_consumed', 'rfid_scan_events', ['consumed'])
    op.create_index('idx_rfid_scan_events_consumed_scanned', 'rfid_scan_events', ['consumed', 'scanned_at'])


def downgrade() -> None:
    op.drop_index('idx_rfid_scan_events_consumed_scanned', table_name='rfid_scan_events')
    op.drop_index('ix_rfid_scan_events_consumed', table_name='rfid_scan_events')
    op.drop_index('ix_rfid_scan_events_scanned_at', table_name='rfid_scan_events')
    op.drop_index('ix_rfid_scan_events_card_id', table_name='rfid_scan_events')
    op.drop_table('rfid_scan_events')
