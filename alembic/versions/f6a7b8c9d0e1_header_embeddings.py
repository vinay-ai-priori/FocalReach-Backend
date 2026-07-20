"""semantic column matching: embedding caches + mapping metadata

- canonical_field_vectors: anchor vectors for each canonical import field (label + synonyms)
- header_embeddings: cache of raw CSV headers already embedded (per model)
- lead_imports.mapping_meta: per-field {confidence, source} powering the mapping UI

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canonical_field_vectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_field", sa.String(100), nullable=False, index=True),
        sa.Column("text", sa.String(255), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("embedding", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("model", "text", name="uq_canonical_vector_model_text"),
    )
    op.create_table(
        "header_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_header", sa.String(255), nullable=False, index=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("embedding", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("model", "normalized_header", name="uq_header_embedding_model_text"),
    )
    op.add_column(
        "lead_imports",
        sa.Column("mapping_meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("lead_imports", "mapping_meta")
    op.drop_table("header_embeddings")
    op.drop_table("canonical_field_vectors")
