"""dedupe active email drafts: enforce at most one active draft per lead

`POST /outreach/imports/{id}/draft` used a check-then-act (SELECT latest
draft, then INSERT) with no DB backing, so concurrent requests for the same
lead (two tabs, a retried request, etc.) could both pass the check and both
insert an active draft. This adds a partial unique index so the invariant
the app already intends -- at most one draft in pending/generating/ready per
lead -- is enforced atomically by Postgres. FAILED stays excluded so the
existing "retry after failure" path keeps working.

Also cleans up any pre-existing duplicates (idempotent -- affects 0 rows if
already clean), keeping the newest draft per lead.

Revision ID: h1a2b3c4d5e6
Revises: g7b8c9d0e1f2
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h1a2b3c4d5e6"
down_revision: Union[str, None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM email_drafts
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY id DESC) AS rn
                FROM email_drafts
            ) t WHERE t.rn > 1
        )
        """
    )
    op.create_index(
        "ux_email_drafts_lead_active",
        "email_drafts",
        ["lead_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'GENERATING', 'READY')"),
    )


def downgrade() -> None:
    op.drop_index("ux_email_drafts_lead_active", table_name="email_drafts")
