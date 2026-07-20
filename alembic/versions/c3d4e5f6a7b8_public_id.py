"""public_id (UUID) on all externally-exposed tables

Adds a stable, non-sequential external identifier alongside the internal integer PK.
Existing rows are backfilled via gen_random_uuid(); the column is NOT NULL + unique.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "tenants",
    "organizations",
    "users",
    "campaigns",
    "website_analyses",
    "company_intelligences",
    "icps",
    "lead_imports",
    "companies",
    "leads",
    "email_drafts",
]


def upgrade() -> None:
    # gen_random_uuid() is built into PostgreSQL 13+; pgcrypto covers older servers.
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    for table in TABLES:
        # Adding a NOT NULL column with a server_default backfills every existing row.
        op.add_column(
            table,
            sa.Column("public_id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        )
        op.create_index(f"ix_{table}_public_id", table, ["public_id"], unique=True)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_public_id", table_name=table)
        op.drop_column(table, "public_id")
