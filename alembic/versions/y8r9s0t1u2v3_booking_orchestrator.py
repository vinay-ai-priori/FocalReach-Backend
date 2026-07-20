"""booking orchestrator: new pending_booking statuses + Cal.com booking outcome fields

Revision ID: y8r9s0t1u2v3
Revises: x7q8r9s0t1u2
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "y8r9s0t1u2v3"
down_revision: Union[str, None] = "x7q8r9s0t1u2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres 12+ allows ADD VALUE inside a transaction (the new values just can't be
    # used within this same transaction — which the migration doesn't do).
    op.execute("ALTER TYPE pending_booking_status ADD VALUE IF NOT EXISTS 'BOOKING'")
    op.execute("ALTER TYPE pending_booking_status ADD VALUE IF NOT EXISTS 'AWAITING_RESLOT'")

    op.add_column("pending_bookings", sa.Column("calcom_booking_uid", sa.String(length=255), nullable=True))
    op.add_column("pending_bookings", sa.Column("meeting_url", sa.String(length=2048), nullable=True))
    op.add_column("pending_bookings", sa.Column("last_error", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("pending_bookings", "last_error")
    op.drop_column("pending_bookings", "meeting_url")
    op.drop_column("pending_bookings", "calcom_booking_uid")
    # Enum values are left in place: Postgres cannot drop enum values, and rows may
    # reference them. Harmless for a downgraded schema.
