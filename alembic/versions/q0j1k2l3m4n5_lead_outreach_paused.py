"""add outreach_paused to leads

Revision ID: q0j1k2l3m4n5
Revises: p9i0j1k2l3m4
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "q0j1k2l3m4n5"
down_revision: Union[str, None] = "p9i0j1k2l3m4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads", sa.Column("outreach_paused", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.alter_column("leads", "outreach_paused", server_default=None)


def downgrade() -> None:
    op.drop_column("leads", "outreach_paused")
