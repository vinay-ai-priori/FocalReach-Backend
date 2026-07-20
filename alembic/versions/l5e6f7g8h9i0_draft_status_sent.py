"""add SENT value to draft_status enum

Revision ID: l5e6f7g8h9i0
Revises: k4d5e6f7g8h9
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "l5e6f7g8h9i0"
down_revision: Union[str, None] = "k4d5e6f7g8h9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the transaction alembic wraps
    # migrations in, so it needs autocommit.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'sent'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; downgrading would require rebuilding
    # the type, which isn't worth it for a status label. No-op.
    pass
