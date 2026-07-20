"""campaigns table + backfill from existing lead imports

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sa.Enum("ACTIVE", "INACTIVE", name="campaign_status").create(op.get_bind(), checkfirst=True)
    from sqlalchemy.dialects.postgresql import ENUM

    campaign_status = ENUM("ACTIVE", "INACTIVE", name="campaign_status", create_type=False)

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("status", campaign_status, nullable=False, server_default="ACTIVE", index=True),
        sa.Column("website_analysis_id", sa.Integer(), sa.ForeignKey("website_analyses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("company_intelligence_id", sa.Integer(), sa.ForeignKey("company_intelligences.id", ondelete="SET NULL"), nullable=True),
        sa.Column("icp_id", sa.Integer(), sa.ForeignKey("icps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lead_import_id", sa.Integer(), sa.ForeignKey("lead_imports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Backfill: one campaign per existing lead import, wired to its full artifact chain.
    op.execute(
        """
        INSERT INTO campaigns
            (user_id, organization_id, name, status, website_analysis_id,
             company_intelligence_id, icp_id, lead_import_id, created_at, updated_at)
        SELECT li.user_id,
               li.organization_id,
               'Legacy campaign — ' || COALESCE(li.filename, 'import ' || li.id),
               'ACTIVE',
               wa.id, ci.id, icp.id, li.id,
               li.created_at, li.updated_at
        FROM lead_imports li
        JOIN icps icp ON icp.id = li.icp_id
        JOIN company_intelligences ci ON ci.id = icp.company_intelligence_id
        JOIN website_analyses wa ON wa.id = ci.website_analysis_id
        """
    )


def downgrade() -> None:
    op.drop_table("campaigns")
    sa.Enum(name="campaign_status").drop(op.get_bind(), checkfirst=True)
