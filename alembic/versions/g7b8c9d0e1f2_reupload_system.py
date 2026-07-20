"""strict re-upload system: pending imports + ICP snapshot fingerprint

- lead_imports.campaign_id: set only on pending re-uploads (candidate dataset awaiting
  confirmation into the campaign's permanent import)
- lead_imports.icp_snapshot_hash: fingerprint of result-affecting ICP fields at run time,
  used to detect "inputs changed" (re-run warning + stale-results banner)

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lead_imports", sa.Column("campaign_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_lead_imports_campaign_id",
        "lead_imports",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_lead_imports_campaign_id", "lead_imports", ["campaign_id"])
    op.add_column("lead_imports", sa.Column("icp_snapshot_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_imports", "icp_snapshot_hash")
    op.drop_index("ix_lead_imports_campaign_id", table_name="lead_imports")
    op.drop_constraint("fk_lead_imports_campaign_id", "lead_imports", type_="foreignkey")
    op.drop_column("lead_imports", "campaign_id")
