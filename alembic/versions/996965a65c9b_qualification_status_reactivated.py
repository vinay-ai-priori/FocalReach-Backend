"""qualification_status_reactivated

Revision ID: 996965a65c9b
Revises: 6ffe5f5d541b
Create Date: 2026-07-20 19:59:43.950379

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '996965a65c9b'
down_revision: Union[str, None] = '6ffe5f5d541b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE must run outside the migration's transaction on PostgreSQL.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE qualification_status ADD VALUE IF NOT EXISTS 'REACTIVATED'")


def downgrade() -> None:
    # Postgres cannot drop an enum value; REACTIVATED simply becomes unused.
    pass
