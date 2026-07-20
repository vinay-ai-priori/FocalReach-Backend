"""Multi-step outreach sequence: channel + step_index on email_drafts, and the
notifications table for follow-up-due nudges.

Fixed step positions per lead: 1 = initial email, 2-4 = follow-ups 1-3 (channel
EMAIL), 5 = LinkedIn message, 6 = call script. Existing rows are the initial
email, so they backfill to (EMAIL, 1) and nothing about the current flow changes.

The old one-active-draft-per-lead partial index widens to one active draft per
(lead, channel, step_index) so each step keeps the same race protection the
initial email has today.

Enum values are UPPERCASE because SQLAlchemy's Enum(PythonEnum) stores member
.name, not .value (see migration s2l3m4n5o6p7).

Revision ID: u4n5o6p7q8r9
Revises: t3m4n5o6p7q8
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "u4n5o6p7q8r9"
down_revision: Union[str, None] = "t3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE draft_channel AS ENUM ('EMAIL', 'LINKEDIN', 'CALL')")
    op.add_column(
        "email_drafts",
        sa.Column(
            "channel",
            sa.Enum("EMAIL", "LINKEDIN", "CALL", name="draft_channel", create_type=False),
            nullable=False,
            server_default="EMAIL",
        ),
    )
    op.add_column(
        "email_drafts", sa.Column("step_index", sa.Integer(), nullable=False, server_default="1")
    )

    # Widen the one-active-draft race guard from per-lead to per-step.
    op.drop_index("ux_email_drafts_lead_active", table_name="email_drafts")
    op.create_index(
        "ux_email_drafts_step_active",
        "email_drafts",
        ["lead_id", "channel", "step_index"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'GENERATING', 'READY')"),
    )
    op.create_index("ix_email_drafts_lead_step", "email_drafts", ["lead_id", "step_index"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
            index=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(50), nullable=False, server_default="follow_up_due"),
        # Which step the nudge is about (2/3/4 = follow-up 1/2/3).
        sa.Column("due_step_index", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # At most one UNREAD notification per (lead, kind) — the hourly beat task can run
    # blindly and duplicates are rejected by the DB.
    op.execute(
        "CREATE UNIQUE INDEX ux_notifications_lead_kind_unread ON notifications (lead_id, kind) "
        "WHERE read_at IS NULL"
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_index("ix_email_drafts_lead_step", table_name="email_drafts")
    op.drop_index("ux_email_drafts_step_active", table_name="email_drafts")
    op.create_index(
        "ux_email_drafts_lead_active",
        "email_drafts",
        ["lead_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'GENERATING', 'READY')"),
    )
    op.drop_column("email_drafts", "step_index")
    op.drop_column("email_drafts", "channel")
    op.execute("DROP TYPE draft_channel")
