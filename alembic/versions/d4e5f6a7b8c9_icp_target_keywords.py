"""target_keywords on icps

Adds a JSONB list of keyword-match terms to the ICP, alongside target_roles.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icps",
        sa.Column("target_keywords", JSONB(), nullable=False, server_default="[]"),
    )
    # Drop the server_default so new rows rely on the app-side default (mirrors other list columns).
    op.alter_column("icps", "target_keywords", server_default=None)


def downgrade() -> None:
    op.drop_column("icps", "target_keywords")
