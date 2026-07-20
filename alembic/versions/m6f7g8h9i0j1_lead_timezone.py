"""add timezone column to leads (cached from country)

Revision ID: m6f7g8h9i0j1
Revises: l5e6f7g8h9i0
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m6f7g8h9i0j1"
down_revision: Union[str, None] = "l5e6f7g8h9i0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("timezone", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "timezone")
