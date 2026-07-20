"""Dispatch scheduling engine: SCHEDULED/SENDING/NEEDS_ATTENTION draft statuses,
scheduled_at slot + owner denormalization + attempt counter + Message-ID on
email_drafts, the dispatch_logs audit table, and the partial unique index that makes
slot double-booking impossible at the DB level (belt-and-braces under the per-user
advisory lock in scheduling_service).

Enum values are UPPERCASE because SQLAlchemy's Enum(PythonEnum) stores member .name,
not .value (see migration s2l3m4n5o6p7).

Revision ID: t3m4n5o6p7q8
Revises: s2l3m4n5o6p7
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t3m4n5o6p7q8"
down_revision: Union[str, None] = "s2l3m4n5o6p7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'SCHEDULED'")
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'SENDING'")
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'NEEDS_ATTENTION'")

    op.add_column("email_drafts", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "email_drafts",
        sa.Column(
            "scheduled_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "email_drafts", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("email_drafts", sa.Column("message_id", sa.String(512), nullable=True))

    # Fast "what's due" scans for the dispatcher poller.
    op.execute(
        "CREATE INDEX ix_email_drafts_due ON email_drafts (scheduled_at) "
        "WHERE status IN ('SCHEDULED', 'SENDING')"
    )
    # One dispatch per (user, instant): even code that bypasses the advisory lock
    # cannot double-book a slot.
    op.execute(
        "CREATE UNIQUE INDEX ux_email_drafts_user_slot ON email_drafts (scheduled_by_user_id, scheduled_at) "
        "WHERE status IN ('SCHEDULED', 'SENDING')"
    )

    op.create_table(
        "dispatch_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("email_drafts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("detail", sa.String(1024), nullable=True),
        sa.Column("message_id", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dispatch_logs")
    op.execute("DROP INDEX IF EXISTS ux_email_drafts_user_slot")
    op.execute("DROP INDEX IF EXISTS ix_email_drafts_due")
    op.drop_column("email_drafts", "message_id")
    op.drop_column("email_drafts", "attempt_count")
    op.drop_column("email_drafts", "scheduled_by_user_id")
    op.drop_column("email_drafts", "scheduled_at")
    # Postgres cannot drop enum values; the added labels stay as harmless orphans.
