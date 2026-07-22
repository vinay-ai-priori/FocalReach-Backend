"""refresh_token_user_agent

Revision ID: b2c4e6f8a1d3
Revises: a1b2c3d4e5f6
Create Date: 2026-07-22 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c4e6f8a1d3'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('refresh_tokens', sa.Column('user_agent', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('refresh_tokens', 'user_agent')
