"""Fuzzy matching of raw CSV headers to canonical fields (deterministic, no AI)."""

import re

from rapidfuzz import fuzz

from app.services.csv.field_definitions import FIELD_DEFINITIONS, FieldDef

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


def match_columns(raw_columns: list[str]) -> dict[str, dict]:
    """Returns {canonical_key: {"csv_column": str | None, "confidence": float}}.

    Greedy assignment: each canonical field gets its best-scoring unused column,
    resolved in descending score order so strong matches claim columns first.
    """
    candidates: list[tuple[float, str, str]] = []  # (score, field_key, column)
    for field_def in FIELD_DEFINITIONS:
        for column in raw_columns:
            score = _score(column, field_def)
            if score >= MATCH_THRESHOLD:
                candidates.append((score, field_def.key, column))

    candidates.sort(key=lambda c: -c[0])
    mapping: dict[str, dict] = {f.key: {"csv_column": None, "confidence": 0.0} for f in FIELD_DEFINITIONS}
    used_columns: set[str] = set()
    assigned_fields: set[str] = set()

    for score, field_key, column in candidates:
        if field_key in assigned_fields or column in used_columns:
            continue
        mapping[field_key] = {"csv_column": column, "confidence": round(score, 1)}
        assigned_fields.add(field_key)
        used_columns.add(column)

    return mapping


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
