"""add mailbox_connections (per-user IMAP/SMTP connections)

Revision ID: k4d5e6f7g8h9
Revises: j3c4d5e6f7g8
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "k4d5e6f7g8h9"
down_revision: Union[str, None] = "j3c4d5e6f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mailbox_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "public_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Enum("GOOGLE", "MICROSOFT", name="mailbox_provider"), nullable=False),
        sa.Column("email_address", sa.String(length=320), nullable=False),
        sa.Column("imap_host", sa.String(length=255), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False),
        sa.Column("smtp_host", sa.String(length=255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("encrypted_app_password", sa.Text(), nullable=False),
        sa.Column("is_connected", sa.Boolean(), nullable=False),
        sa.Column("last_verification_error", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "email_address", name="uq_mailbox_user_email"),
    )
    op.create_index(op.f("ix_mailbox_connections_user_id"), "mailbox_connections", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_mailbox_connections_public_id"), "mailbox_connections", ["public_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mailbox_connections_public_id"), table_name="mailbox_connections")
    op.drop_index(op.f("ix_mailbox_connections_user_id"), table_name="mailbox_connections")
    op.drop_table("mailbox_connections")
    op.execute("DROP TYPE IF EXISTS mailbox_provider")
