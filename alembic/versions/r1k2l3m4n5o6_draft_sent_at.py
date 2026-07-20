"""add sent_at to email_drafts

Revision ID: r1k2l3m4n5o6
Revises: q0j1k2l3m4n5
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "r1k2l3m4n5o6"
down_revision: Union[str, None] = "q0j1k2l3m4n5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_drafts", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("email_drafts", "sent_at")
