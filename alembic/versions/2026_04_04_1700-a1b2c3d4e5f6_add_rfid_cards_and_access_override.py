"""add rfid_cards and access_override tables

Revision ID: a1b2c3d4e5f6
Revises: 79456f6cbdfa
Create Date: 2026-04-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '79456f6cbdfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # rfid_cards table
    op.create_table(
        'rfid_cards',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('card_id', sa.String(20), nullable=False, unique=True),
        sa.Column('username', sa.String(100), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_rfid_cards_card_id', 'rfid_cards', ['card_id'])
    op.create_index('idx_rfid_cards_user_id', 'rfid_cards', ['user_id'])

    # access_override table
    op.create_table(
        'access_override',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('mode', sa.String(20), nullable=False, server_default='NORMAL'),
        sa.Column('set_by_user_id', sa.String(50), nullable=False),
        sa.Column('set_by_username', sa.String(100), nullable=False),
        sa.Column('set_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # Seed a default NORMAL row so the connector always has a row to read
    op.execute(
        "INSERT INTO access_override (mode, set_by_user_id, set_by_username) "
        "VALUES ('NORMAL', 'system', 'system')"
    )


def downgrade() -> None:
    op.drop_index('idx_rfid_cards_user_id', table_name='rfid_cards')
    op.drop_index('idx_rfid_cards_card_id', table_name='rfid_cards')
    op.drop_table('rfid_cards')
    op.drop_table('access_override')
