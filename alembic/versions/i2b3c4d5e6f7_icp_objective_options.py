"""add icps.campaign_objective_options (AI candidate objectives)

ICP generation now produces 3 candidate campaign objectives instead of one; the user
picks/edits one as `campaign_objective` as before, and the candidates are kept in this
new column so the picker still shows them after a page reload.

Revision ID: i2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "i2b3c4d5e6f7"
down_revision: Union[str, None] = "h1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icps",
        sa.Column(
            "campaign_objective_options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("icps", "campaign_objective_options")
