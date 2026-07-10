"""Embeddings for semantic CSV-header matching, with a Postgres cache.

Cost profile: text-embedding-3-small is $0.02 / 1M tokens; a header is ~4 tokens and
all unmatched headers of an upload go out in a single batched API call, so an upload
costs fractions of a cent at worst and $0 once the caches are warm. Canonical anchor
vectors (field label + synonyms) are seeded lazily on first use and re-used forever.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger
from app.models.header_embedding import CanonicalFieldVector, HeaderEmbedding
from app.services.ai.openai_client import _get_client
from app.services.csv.field_definitions import FIELD_DEFINITIONS

logger = get_logger(__name__)


def normalize_header(header: str) -> str:
    header = header.strip().lower()
    header = re.sub(r"[_\-/]+", " ", header)
    header = re.sub(r"\s+", " ", header)
    return header


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """One API call for the whole batch; order of vectors matches input order."""
    response = _get_client().embeddings.create(model=settings.OPENAI_EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def get_canonical_vectors(db: Session) -> list[tuple[str, list[float]]]:
    """(canonical_field, vector) anchors for every field. Lazily seeds any anchor text
    not yet embedded (first run, or after synonyms change), keyed by embedding model."""
    model = settings.OPENAI_EMBEDDING_MODEL
    wanted: dict[str, str] = {}  # anchor text -> canonical field key
    for f in FIELD_DEFINITIONS:
        for text in {f.label.lower(), *f.synonyms}:
            wanted[normalize_header(text)] = f.key

    existing = db.scalars(select(CanonicalFieldVector).where(CanonicalFieldVector.model == model)).all()
    existing_by_text = {row.text: row for row in existing}

    missing = [t for t in wanted if t not in existing_by_text]
    if missing:
        vectors = _embed_batch(missing)
        for text, vector in zip(missing, vectors):
            row = CanonicalFieldVector(
                canonical_field=wanted[text], text=text, model=model, embedding=vector
            )
            db.add(row)
            existing_by_text[text] = row
        db.commit()
        logger.info("Seeded %s canonical field vectors (%s)", len(missing), model)

    return [(row.canonical_field, row.embedding) for row in existing_by_text.values()]


def get_header_vectors(db: Session, headers: list[str]) -> dict[str, list[float]]:
    """{normalized header -> vector}, hitting the Postgres cache first and embedding
    only the misses in a single batched call."""
    model = settings.OPENAI_EMBEDDING_MODEL
    normalized = list({normalize_header(h) for h in headers if h.strip()})
    if not normalized:
        return {}

    cached = db.scalars(
        select(HeaderEmbedding).where(
            HeaderEmbedding.model == model, HeaderEmbedding.normalized_header.in_(normalized)
        )
    ).all()
    result = {row.normalized_header: row.embedding for row in cached}

    missing = [h for h in normalized if h not in result]
    if missing:
        vectors = _embed_batch(missing)
        for header, vector in zip(missing, vectors):
            db.add(HeaderEmbedding(normalized_header=header, model=model, embedding=vector))
            result[header] = vector
        db.commit()
        logger.info("Embedded %s new headers (%s cached)", len(missing), len(cached))

    return result


def cosine(a: list[float], b: list[float]) -> float:
    # Embedding vectors from OpenAI are unit-normalized, so the dot product IS the
    # cosine similarity — no norm division needed.
    return sum(x * y for x, y in zip(a, b))
