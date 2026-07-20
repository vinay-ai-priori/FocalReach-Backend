"""inbox reply poller: mailbox polling cursor, inbound_replies, pending_bookings,
notification kind generalization

Revision ID: w6p7q8r9s0t1
Revises: v5o6p7q8r9s0
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "w6p7q8r9s0t1"
down_revision: Union[str, None] = "v5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mailbox_connections", sa.Column("imap_uidvalidity", sa.Integer(), nullable=True))
    op.add_column("mailbox_connections", sa.Column("last_polled_uid", sa.Integer(), nullable=True))
    op.add_column(
        "mailbox_connections", sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.alter_column("notifications", "due_step_index", existing_type=sa.Integer(), nullable=True)
    op.add_column("notifications", sa.Column("detail", sa.String(length=500), nullable=True))

    op.create_table(
        "inbound_replies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "public_id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("mailbox_connection_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("matched_draft_id", sa.Integer(), nullable=True),
        sa.Column("imap_uid", sa.Integer(), nullable=False),
        sa.Column("imap_message_id", sa.String(length=998), nullable=False),
        sa.Column("in_reply_to", sa.String(length=998), nullable=True),
        sa.Column("from_address", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=998), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "intent", sa.Enum("NEGATIVE", "NEUTRAL", "POSITIVE", "BOOKED", name="reply_intent"), nullable=True
        ),
        sa.Column("intent_confidence", sa.Float(), nullable=True),
        sa.Column("intent_reason", sa.String(length=1024), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mailbox_connection_id"], ["mailbox_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_draft_id"], ["email_drafts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mailbox_connection_id", "imap_message_id", name="uq_inbound_reply_mailbox_message"
        ),
    )
    op.create_index(op.f("ix_inbound_replies_mailbox_connection_id"), "inbound_replies", ["mailbox_connection_id"], unique=False)
    op.create_index(op.f("ix_inbound_replies_lead_id"), "inbound_replies", ["lead_id"], unique=False)
    op.create_index(op.f("ix_inbound_replies_public_id"), "inbound_replies", ["public_id"], unique=True)

    op.create_table(
        "pending_bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "public_id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("inbound_reply_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "NEEDS_REVIEW", "BOOKED", "CANCELLED", name="pending_booking_status"),
            nullable=False,
        ),
        sa.Column("resolved_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_timezone", sa.String(length=64), nullable=True),
        sa.Column(
            "timezone_source",
            sa.Enum("EXPLICIT", "LEAD_COUNTRY", "UNKNOWN", name="timezone_source"),
            nullable=True,
        ),
        sa.Column("raw_extraction", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inbound_reply_id"], ["inbound_replies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pending_bookings_lead_id"), "pending_bookings", ["lead_id"], unique=False)
    op.create_index(op.f("ix_pending_bookings_inbound_reply_id"), "pending_bookings", ["inbound_reply_id"], unique=False)
    op.create_index(op.f("ix_pending_bookings_user_id"), "pending_bookings", ["user_id"], unique=False)
    op.create_index(op.f("ix_pending_bookings_public_id"), "pending_bookings", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_pending_bookings_public_id"), table_name="pending_bookings")
    op.drop_index(op.f("ix_pending_bookings_user_id"), table_name="pending_bookings")
    op.drop_index(op.f("ix_pending_bookings_inbound_reply_id"), table_name="pending_bookings")
    op.drop_index(op.f("ix_pending_bookings_lead_id"), table_name="pending_bookings")
    op.drop_table("pending_bookings")
    op.execute("DROP TYPE IF EXISTS pending_booking_status")
    op.execute("DROP TYPE IF EXISTS timezone_source")

    op.drop_index(op.f("ix_inbound_replies_public_id"), table_name="inbound_replies")
    op.drop_index(op.f("ix_inbound_replies_lead_id"), table_name="inbound_replies")
    op.drop_index(op.f("ix_inbound_replies_mailbox_connection_id"), table_name="inbound_replies")
    op.drop_table("inbound_replies")
    op.execute("DROP TYPE IF EXISTS reply_intent")

    op.drop_column("notifications", "detail")
    op.alter_column("notifications", "due_step_index", existing_type=sa.Integer(), nullable=False)

    op.drop_column("mailbox_connections", "last_polled_at")
    op.drop_column("mailbox_connections", "last_polled_uid")
    op.drop_column("mailbox_connections", "imap_uidvalidity")
