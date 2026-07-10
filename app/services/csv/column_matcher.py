"""Matching of raw CSV headers to canonical fields.

Three-tier cascade, cheapest first:
1. exact/alias match (deterministic, free)
2. rapidfuzz token-sort ratio (deterministic, free)
3. embedding cosine similarity (OpenAI text-embedding-3-small, Postgres-cached,
   ~$0 per upload) — catches semantic matches with no lexical overlap, e.g.
   "Org" -> company_name, "Headcount" -> employee_count, "Designation" -> title.

Tier 3 runs only for fields/columns still unmatched after tiers 1-2 and fails open:
any embedding error degrades to the deterministic behavior, never blocks an upload.
"""

import re

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.services.csv.field_definitions import FIELD_DEFINITIONS, FieldDef

logger = get_logger(__name__)

MATCH_THRESHOLD = 82.0


def _normalize(header: str) -> str:
    header = header.strip().lower()
    header = re.sub(r"[_\-/]+", " ", header)
    header = re.sub(r"\s+", " ", header)
    return header


def _score(header: str, field_def: FieldDef) -> float:
    normalized = _normalize(header)
    # Exact-priority aliases win outright (e.g. "Primary Email" over "Email 9"),
    # earlier aliases ranking above later ones ("Company Name - Cleaned" over "Company Name")
    for rank, alias in enumerate(field_def.aliases_exact_priority):
        if normalized == _normalize(alias):
            return 100.0 - rank * 0.1
    best = 0.0
    for synonym in field_def.synonyms:
        if normalized == _normalize(synonym):
            return 99.0
        best = max(best, fuzz.token_sort_ratio(normalized, synonym))
    return best


def match_columns(raw_columns: list[str], db: Session | None = None) -> dict[str, dict]:
    """Returns {canonical_key: {"csv_column": str | None, "confidence": float, "source": str | None}}.

    Greedy assignment: each canonical field gets its best-scoring unused column,
    resolved in descending score order so strong matches claim columns first.
    When a db session is provided, unmatched fields get a semantic (embedding) pass.
    """
    candidates: list[tuple[float, str, str]] = []  # (score, field_key, column)
    for field_def in FIELD_DEFINITIONS:
        for column in raw_columns:
            score = _score(column, field_def)
            if score >= MATCH_THRESHOLD:
                candidates.append((score, field_def.key, column))

    candidates.sort(key=lambda c: -c[0])
    mapping: dict[str, dict] = {
        f.key: {"csv_column": None, "confidence": 0.0, "source": None} for f in FIELD_DEFINITIONS
    }
    used_columns: set[str] = set()
    assigned_fields: set[str] = set()

    for score, field_key, column in candidates:
        if field_key in assigned_fields or column in used_columns:
            continue
        source = "exact" if score >= 99.0 else "fuzzy"
        mapping[field_key] = {"csv_column": column, "confidence": round(score, 1), "source": source}
        assigned_fields.add(field_key)
        used_columns.add(column)

    if db is not None:
        _apply_semantic_tier(db, raw_columns, mapping, used_columns, assigned_fields)

    return mapping


def _apply_semantic_tier(
    db: Session,
    raw_columns: list[str],
    mapping: dict[str, dict],
    used_columns: set[str],
    assigned_fields: set[str],
) -> None:
    """Embedding fallback for fields/columns tiers 1-2 couldn't place. Fail-open."""
    remaining_columns = [c for c in raw_columns if c not in used_columns]
    remaining_fields = [f.key for f in FIELD_DEFINITIONS if f.key not in assigned_fields]
    if not remaining_columns or not remaining_fields:
        return

    try:
        from app.services.ai.embedding_service import (
            cosine,
            get_canonical_vectors,
            get_header_vectors,
            normalize_header,
        )

        anchors = [(field, vec) for field, vec in get_canonical_vectors(db) if field in remaining_fields]
        header_vectors = get_header_vectors(db, remaining_columns)
        if not anchors or not header_vectors:
            return

        threshold = settings.SEMANTIC_MATCH_THRESHOLD
        candidates: list[tuple[float, str, str]] = []  # (similarity, field_key, column)
        for column in remaining_columns:
            vec = header_vectors.get(normalize_header(column))
            if vec is None:
                continue
            best_per_field: dict[str, float] = {}
            for field_key, anchor_vec in anchors:
                sim = cosine(vec, anchor_vec)
                if sim > best_per_field.get(field_key, 0.0):
                    best_per_field[field_key] = sim
            for field_key, sim in best_per_field.items():
                if sim >= threshold:
                    candidates.append((sim, field_key, column))

        candidates.sort(key=lambda c: -c[0])
        for sim, field_key, column in candidates:
            if field_key in assigned_fields or column in used_columns:
                continue
            mapping[field_key] = {
                "csv_column": column,
                # Map cosine [threshold..1] to a display confidence capped below
                # fuzzy-tier scores so semantic matches read as "probable, verify".
                "confidence": round(min(80.0, sim * 100), 1),
                "source": "semantic",
            }
            assigned_fields.add(field_key)
            used_columns.add(column)
            logger.info("Semantic match: '%s' -> %s (%.2f)", column, field_key, sim)
    except Exception as exc:
        logger.warning("Semantic column matching unavailable, using deterministic tiers only: %s", exc)


def build_missing_field_report(mapping: dict[str, dict]) -> list[dict]:
    """Missing-field warnings with the concrete consequence of continuing anyway."""
    report = []
    # Company size only needs ONE of exact count / range — qualification uses count and
    # falls back to range. If either is mapped, don't nag about the other.
    count_present = bool(mapping.get("company_employee_count", {}).get("csv_column"))
    range_present = bool(mapping.get("company_employee_range", {}).get("csv_column"))
    for field_def in FIELD_DEFINITIONS:
        if mapping.get(field_def.key, {}).get("csv_column"):
            continue
        if not field_def.consequence_if_missing:
            continue
        if field_def.key == "company_employee_count" and range_present:
            continue
        if field_def.key == "company_employee_range" and count_present:
            continue
        report.append(
            {
                "canonical_field": field_def.key,
                "label": field_def.label,
                "severity": "critical" if field_def.is_mandatory else "warning",
                "required_for": field_def.required_for,
                "consequence": field_def.consequence_if_missing,
            }
        )
    return report
