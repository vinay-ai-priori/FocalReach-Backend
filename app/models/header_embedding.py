"""Embedding caches for semantic CSV-header matching.

Vectors are stored as JSONB float arrays; at this scale (tens of canonical anchors,
a few thousand distinct headers) brute-force cosine in Python is microseconds, so no
pgvector/ANN index is needed. If canonical vectors ever grow to tens of thousands of
rows, switch the column to pgvector and add an HNSW index.
"""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CanonicalFieldVector(Base, TimestampMixin):
    """One anchor vector per (canonical field, anchor text). A field has multiple
    anchors (its label + every synonym); a header matches the field via max similarity."""

    __tablename__ = "canonical_field_vectors"
    __table_args__ = (UniqueConstraint("model", "text", name="uq_canonical_vector_model_text"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_field: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    text: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[list] = mapped_column(JSONB, nullable=False)


class HeaderEmbedding(Base, TimestampMixin):
    """Cache of raw CSV headers already embedded. Header vocabulary in B2B exports is
    small, so the hit rate approaches 100% over time and most uploads cost $0."""

    __tablename__ = "header_embeddings"
    __table_args__ = (UniqueConstraint("model", "normalized_header", name="uq_header_embedding_model_text"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_header: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[list] = mapped_column(JSONB, nullable=False)
