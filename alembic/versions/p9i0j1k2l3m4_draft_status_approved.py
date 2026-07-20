"""add APPROVED value to draft_status enum

Revision ID: p9i0j1k2l3m4
Revises: o8h9i0j1k2l3
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "p9i0j1k2l3m4"
down_revision: Union[str, None] = "o8h9i0j1k2l3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'approved'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; downgrading would require rebuilding the
    # type, which isn't worth it for a status label. No-op.
    pass
