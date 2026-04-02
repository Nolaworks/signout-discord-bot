"""baseline schema

Revision ID: 61167fdff2f5
Revises: 
Create Date: 2026-03-10 13:32:20.117718

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61167fdff2f5'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline migration: database already matches models.
    # This revision exists only as the starting point for Alembic history.
    # Stamp with:  alembic stamp 61167fdff2f5
    pass


def downgrade() -> None:
    # Cannot downgrade past the baseline.
    pass
