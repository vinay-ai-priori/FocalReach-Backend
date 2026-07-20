"""lead_tier_reactivated

Revision ID: 6158ee4e0057
Revises: 996965a65c9b
Create Date: 2026-07-20 22:53:18.197393

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6158ee4e0057'
down_revision: Union[str, None] = '996965a65c9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE must run outside the migration's transaction on PostgreSQL.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE lead_tier ADD VALUE IF NOT EXISTS 'REACTIVATED'")


def downgrade() -> None:
    # Postgres cannot drop an enum value; REACTIVATED simply becomes unused.
    pass
