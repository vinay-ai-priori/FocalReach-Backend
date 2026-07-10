"""CSV parsing, validation, and confirmed import into companies + leads."""

import csv
import io
import re

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationFailedError
from app.core.logging import get_logger
from app.models.company import Company
from app.models.icp import ICP
from app.models.lead import Lead
from app.models.lead_import import ImportStatus, LeadImport
from app.repositories.lead_import_repository import LeadImportRepository
from app.services.csv.column_matcher import build_missing_field_report, match_columns
from app.services.csv.dedup_service import build_dedup_index
from app.services.website.url_validator import extract_domain

logger = get_logger(__name__)

MAX_ROWS = 20000


def parse_and_validate(
    db: Session,
    icp: ICP,
    filename: str,
    file_bytes: bytes,
    user_id: int | None = None,
    organization_id: int | None = None,
) -> LeadImport:
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationFailedError("The CSV file has no header row.")

    columns = [c.strip() for c in reader.fieldnames if c and c.strip()]
    rows = []
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            raise ValidationFailedError(f"CSV exceeds the {MAX_ROWS} row limit for a single import.")
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})

    if not rows:
        raise ValidationFailedError("The CSV file contains no data rows.")

    matching = match_columns(columns, db=db)
    column_mapping = {key: val["csv_column"] for key, val in matching.items()}
    mapping_meta = {
        key: {"confidence": val["confidence"], "source": val.get("source")}
        for key, val in matching.items()
        if val["csv_column"]
    }
    missing = build_missing_field_report(matching)

    lead_import = LeadImport(
        icp_id=icp.id,
        user_id=user_id,
        organization_id=organization_id,
        filename=filename,
        status=ImportStatus.MAPPING,
        total_rows=len(rows),
        raw_columns=columns,
        column_mapping=column_mapping,
        mapping_meta=mapping_meta,
        missing_fields=missing,
        raw_rows=rows,
    )
    return LeadImportRepository(db).create(lead_import)


def compute_stats(lead_import: LeadImport) -> dict:
    """CSV analytics for the validation page, computed from the current column mapping so
    they refresh whenever the user remaps a column. Uses the raw rows still held pre-confirm."""
    rows = lead_import.raw_rows or []
    mapping = lead_import.column_mapping or {}

    companies: set[str] = set()
    kept = 0
    drop_no_company = drop_no_name = drop_no_email = 0

    for row in rows:
        verdict = classify_row(row, mapping)
        if verdict == "no_company":
            drop_no_company += 1
            continue
        if verdict == "no_name":
            drop_no_name += 1
            continue
        if verdict == "no_email":
            drop_no_email += 1
            continue
        # kept row
        kept += 1
        companies.add(_get(row, mapping, "company_name").lower())

    return {
        "rows_detected": lead_import.total_rows,
        "columns_detected": len(lead_import.raw_columns or []),
        "unique_companies": len(companies),
        "total_leads": kept,  # rows that will actually be imported
        "rows_dropped": drop_no_company + drop_no_name + drop_no_email,
        "dropped_missing_company": drop_no_company,
        "dropped_missing_name": drop_no_name,
        "dropped_missing_email": drop_no_email,
    }


def update_mapping(db: Session, lead_import: LeadImport, column_mapping: dict[str, str | None]) -> LeadImport:
    valid_columns = set(lead_import.raw_columns)
    for key, column in column_mapping.items():
        if column is not None and column not in valid_columns:
            raise ValidationFailedError(f"Column '{column}' does not exist in the uploaded CSV.")
    merged = {**lead_import.column_mapping, **column_mapping}
    meta = dict(lead_import.mapping_meta or {})
    for key, column in column_mapping.items():
        if column:
            meta[key] = {"confidence": 100.0, "source": "manual"}
        else:
            meta.pop(key, None)
    matching = {k: {"csv_column": v, "confidence": 100.0 if v else 0.0} for k, v in merged.items()}
    missing = build_missing_field_report(matching)
    return LeadImportRepository(db).update(
        lead_import, column_mapping=merged, mapping_meta=meta, missing_fields=missing
    )


def _get(row: dict, mapping: dict, key: str) -> str | None:
    column = mapping.get(key)
    if not column:
        return None
    value = (row.get(column) or "").strip()
    return value or None


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def classify_row(row: dict, mapping: dict) -> str:
    """A row is only imported when it can be identified and contacted.
    Returns 'keep' or a drop reason: 'no_company' | 'no_name' | 'no_email'."""
    if not _get(row, mapping, "company_name"):
        return "no_company"
    if not _get(row, mapping, "full_name"):
        return "no_name"
    if not _get(row, mapping, "email"):
        return "no_email"
    return "keep"


def confirm_import(db: Session, lead_import: LeadImport) -> LeadImport:
    """Materialize raw rows into Company and Lead records, deduplicating companies."""
    if lead_import.status != ImportStatus.MAPPING:
        return lead_import
    mapping = lead_import.column_mapping
    if not mapping.get("company_name"):
        raise ValidationFailedError("A Company Name column must be mapped before import.")

    # Cross-campaign dedup index for the organization's OTHER campaigns (built once).
    dedup_index = build_dedup_index(db, lead_import.organization_id, lead_import.id)

    companies_by_key: dict[str, Company] = {}
    leads: list[Lead] = []
    dropped = 0
    duplicates = 0

    for row in lead_import.raw_rows or []:
        # Drop rows we can't identify or contact (missing company name, name, or email).
        if classify_row(row, mapping) != "keep":
            dropped += 1
            continue

        company_name = _get(row, mapping, "company_name")
        company_key = company_name.lower()
        company = companies_by_key.get(company_key)
        if company is None:
            website = _get(row, mapping, "company_website")
            domain = None
            if website:
                try:
                    domain = extract_domain(website)
                except Exception:
                    domain = None
            company = Company(
                lead_import_id=lead_import.id,
                name=company_name,
                website=website,
                domain=domain,
                industry=_get(row, mapping, "company_industry"),
                description=_get(row, mapping, "company_description"),
                city=_get(row, mapping, "company_city"),
                state=_get(row, mapping, "company_state"),
                country=_get(row, mapping, "company_country"),
                employee_count=_parse_int(_get(row, mapping, "company_employee_count")),
                employee_range=_get(row, mapping, "company_employee_range"),
                annual_revenue=_get(row, mapping, "company_revenue"),
                linkedin_url=_get(row, mapping, "company_linkedin"),
            )
            companies_by_key[company_key] = company
            db.add(company)
            db.flush()

        full_name = _get(row, mapping, "full_name")
        # We only collect Full Name; derive first/last from it for greetings & personalization.
        name_parts = full_name.split()
        first_name = name_parts[0] if name_parts else None
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else None

        email = _get(row, mapping, "email")
        is_duplicate, dup_reason, _ = dedup_index.evaluate(
            company.name, company.domain, email, full_name
        )
        if is_duplicate:
            duplicates += 1

        leads.append(
            Lead(
                lead_import_id=lead_import.id,
                company_id=company.id,
                full_name=full_name,
                first_name=first_name,
                last_name=last_name,
                title=_get(row, mapping, "title"),
                seniority=_get(row, mapping, "seniority"),
                department=_get(row, mapping, "department"),
                email=email,
                phone=_get(row, mapping, "phone"),
                linkedin_url=_get(row, mapping, "linkedin_url"),
                country=_get(row, mapping, "contact_country"),
                time_in_role=_get(row, mapping, "time_in_role"),
                time_at_company=_get(row, mapping, "time_at_company"),
                years_experience=_get(row, mapping, "years_experience"),
                is_duplicate=is_duplicate,
                duplicate_reason=dup_reason,
            )
        )

    db.add_all(leads)
    lead_import.status = ImportStatus.IMPORTED
    lead_import.raw_rows = None  # drop the raw payload once materialized
    db.commit()
    db.refresh(lead_import)
    logger.info(
        "Import %s confirmed: %s companies, %s leads, %s dropped, %s duplicates eliminated",
        lead_import.id, len(companies_by_key), len(leads), dropped, duplicates,
    )
    return lead_import
