"""add refine_count to email_drafts (regenerate/shorter/etc quota)

Revision ID: o8h9i0j1k2l3
Revises: n7g8h9i0j1k2
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "o8h9i0j1k2l3"
down_revision: Union[str, None] = "n7g8h9i0j1k2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_drafts", sa.Column("refine_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.alter_column("email_drafts", "refine_count", server_default=None)


def downgrade() -> None:
    op.drop_column("email_drafts", "refine_count")
