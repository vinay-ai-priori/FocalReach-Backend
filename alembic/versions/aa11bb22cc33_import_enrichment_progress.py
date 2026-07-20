"""lead_imports: enrichment progress counters for live qualification UX

Revision ID: a1b2c3d4e5f6
Revises: z9s0t1u2v3w4
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "aa11bb22cc33"
down_revision: Union[str, None] = "a0t1u2v3w4x5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lead_imports", sa.Column("enrichment_total", sa.Integer(), nullable=True))
    op.add_column(
        "lead_imports",
        sa.Column("enrichment_done", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("lead_imports", "enrichment_done")
    op.drop_column("lead_imports", "enrichment_total")
