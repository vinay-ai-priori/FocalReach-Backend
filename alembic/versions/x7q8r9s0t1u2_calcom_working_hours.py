"""add working hours / schedule fields to calcom_connections

Revision ID: x7q8r9s0t1u2
Revises: w6p7q8r9s0t1
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "x7q8r9s0t1u2"
down_revision: Union[str, None] = "w6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_WORKING_DAYS = '["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]'


def upgrade() -> None:
    op.add_column(
        "calcom_connections",
        sa.Column("working_days", JSONB(), nullable=False, server_default=sa.text(f"'{DEFAULT_WORKING_DAYS}'")),
    )
    op.add_column(
        "calcom_connections",
        sa.Column("working_hours_start", sa.String(length=5), nullable=False, server_default="09:00"),
    )
    op.add_column(
        "calcom_connections",
        sa.Column("working_hours_end", sa.String(length=5), nullable=False, server_default="17:00"),
    )
    op.add_column("calcom_connections", sa.Column("calcom_schedule_id", sa.Integer(), nullable=True))
    op.add_column("calcom_connections", sa.Column("calcom_schedule_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("calcom_connections", "calcom_schedule_name")
    op.drop_column("calcom_connections", "calcom_schedule_id")
    op.drop_column("calcom_connections", "working_hours_end")
    op.drop_column("calcom_connections", "working_hours_start")
    op.drop_column("calcom_connections", "working_days")
