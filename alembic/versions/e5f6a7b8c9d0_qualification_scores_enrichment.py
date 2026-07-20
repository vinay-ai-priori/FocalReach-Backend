"""qualification scores + structured enrichment + document-based lead scoring

- companies: LLM qualification scores (industry match, company fit, reasoning) and the
  structured enrichment profile used to compute them.
- leads: total years of experience (CSV field) and the new signal / company-fit score
  dimensions from the Role Score & Signal Score logic document.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("industry_match_score", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("company_fit_score", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("qualification_reasoning", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("enrichment_profile", JSONB(), nullable=True))

    op.add_column("leads", sa.Column("years_experience", sa.String(100), nullable=True))
    op.add_column("leads", sa.Column("signal_score", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("company_fit_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "company_fit_score")
    op.drop_column("leads", "signal_score")
    op.drop_column("leads", "years_experience")

    op.drop_column("companies", "enrichment_profile")
    op.drop_column("companies", "qualification_reasoning")
    op.drop_column("companies", "company_fit_score")
    op.drop_column("companies", "industry_match_score")
