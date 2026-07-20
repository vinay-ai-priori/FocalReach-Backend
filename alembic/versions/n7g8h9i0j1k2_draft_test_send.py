"""add last_test_email / last_test_sent_at to email_drafts

Revision ID: n7g8h9i0j1k2
Revises: m6f7g8h9i0j1
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "n7g8h9i0j1k2"
down_revision: Union[str, None] = "m6f7g8h9i0j1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_drafts", sa.Column("last_test_email", sa.String(length=320), nullable=True))
    op.add_column(
        "email_drafts", sa.Column("last_test_sent_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("email_drafts", "last_test_sent_at")
    op.drop_column("email_drafts", "last_test_email")
