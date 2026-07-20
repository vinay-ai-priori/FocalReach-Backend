"""companies: solvable pain points extracted during qualification scoring

Revision ID: a0t1u2v3w4x5
Revises: z9s0t1u2v3w4
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a0t1u2v3w4x5"
down_revision: Union[str, None] = "z9s0t1u2v3w4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("solvable_pain_points", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "solvable_pain_points")
