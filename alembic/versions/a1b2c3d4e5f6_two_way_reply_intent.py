"""two_way_reply_intent

Collapses the reply-intent taxonomy to NEED_REPLY / BOOKING_PENDING, adds the
NEEDS_REPLY pending-booking status (Discovery "Need Reply" list), and records how each
reply's intent was detected (intent_detection).

Revision ID: a1b2c3d4e5f6
Revises: 6158ee4e0057
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '6158ee4e0057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inbound_replies",
        sa.Column("intent_detection", sa.String(length=32), nullable=True),
    )

    # Swap reply_intent to the new two-value enum by creating a fresh type and casting
    # the column across, remapping existing rows: BOOKED -> BOOKING_PENDING, all other
    # non-null values -> NEED_REPLY, NULL stays NULL.
    op.execute("CREATE TYPE reply_intent_new AS ENUM ('NEED_REPLY', 'BOOKING_PENDING')")
    op.execute(
        "ALTER TABLE inbound_replies ALTER COLUMN intent TYPE reply_intent_new USING ("
        "  CASE"
        "    WHEN intent IS NULL THEN NULL"
        "    WHEN intent::text = 'BOOKED' THEN 'BOOKING_PENDING'"
        "    ELSE 'NEED_REPLY'"
        "  END::reply_intent_new"
        ")"
    )
    op.execute("DROP TYPE reply_intent")
    op.execute("ALTER TYPE reply_intent_new RENAME TO reply_intent")

    # New pending-booking status for "Need Reply" rows.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE pending_booking_status ADD VALUE IF NOT EXISTS 'NEEDS_REPLY'")


def downgrade() -> None:
    op.execute("CREATE TYPE reply_intent_old AS ENUM ('NEGATIVE', 'NEUTRAL', 'POSITIVE', 'BOOKED')")
    op.execute(
        "ALTER TABLE inbound_replies ALTER COLUMN intent TYPE reply_intent_old USING ("
        "  CASE"
        "    WHEN intent IS NULL THEN NULL"
        "    WHEN intent::text = 'BOOKING_PENDING' THEN 'BOOKED'"
        "    ELSE 'NEUTRAL'"
        "  END::reply_intent_old"
        ")"
    )
    op.execute("DROP TYPE reply_intent")
    op.execute("ALTER TYPE reply_intent_old RENAME TO reply_intent")

    op.drop_column("inbound_replies", "intent_detection")
    # Postgres cannot drop the NEEDS_REPLY enum value; it simply becomes unused.
