"""add calcom_connections (per-user Cal.com OAuth connection)

Revision ID: v5o6p7q8r9s0
Revises: u4n5o6p7q8r9
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "v5o6p7q8r9s0"
down_revision: Union[str, None] = "u4n5o6p7q8r9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calcom_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "public_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("calcom_user_email", sa.String(length=320), nullable=True),
        sa.Column("calcom_username", sa.String(length=255), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=512), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("selected_event_type_id", sa.Integer(), nullable=True),
        sa.Column("selected_event_type_slug", sa.String(length=255), nullable=True),
        sa.Column("selected_event_type_title", sa.String(length=255), nullable=True),
        sa.Column("is_connected", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_calcom_connections_user_id"),
    )
    op.create_index(op.f("ix_calcom_connections_user_id"), "calcom_connections", ["user_id"], unique=True)
    op.create_index(
        op.f("ix_calcom_connections_public_id"), "calcom_connections", ["public_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_calcom_connections_public_id"), table_name="calcom_connections")
    op.drop_index(op.f("ix_calcom_connections_user_id"), table_name="calcom_connections")
    op.drop_table("calcom_connections")
