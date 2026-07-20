"""global companies: cross-campaign enrichment cache with freshness TTL

Revision ID: z9s0t1u2v3w4
Revises: y8r9s0t1u2v3
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "z9s0t1u2v3w4"
down_revision: Union[str, None] = "y8r9s0t1u2v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "global_companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=True),
        sa.Column("website", sa.String(length=2048), nullable=True),
        sa.Column("enrichment_profile", JSONB(), nullable=True),
        sa.Column("enrichment_content", sa.Text(), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_till", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_global_companies_domain", "global_companies", ["domain"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_global_companies_domain", table_name="global_companies")
    op.drop_table("global_companies")
