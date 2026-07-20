"""Strict re-upload rules for campaigns that already ran.

Identity is content-based, per ROW — never per file. A lead row is "existing" when the
same contact (email first, then normalized name) at the same company (domain first, then
normalized name) is already in the campaign's dataset, regardless of column order,
renamed headers, or how the file was exported.

Decision matrix (mode computed at validation time AND re-checked at confirm):
  inputs unchanged + 0 new leads   -> blocked  (nothing to run)
  inputs unchanged + >=1 new leads -> append   (only new entities enter the pipeline)
  inputs changed   + anything      -> rerun    (explicit warning; computed results reset,
                                                entities kept, everything re-qualified)
"""

import hashlib
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyQualification, QualificationStatus
from app.models.email_draft import EmailDraft
from app.models.icp import ICP
from app.models.lead import Lead
from app.models.lead_import import LeadImport
from app.services.csv.dedup_service import _norm_name, company_keys
from app.services.website.url_validator import extract_domain

# ICP fields that affect qualification gates, LLM fit scoring, or lead scoring.
# (target_roles / target_seniorities / outreach_tone only affect email drafting,
# which is regenerated on demand from the Outreach page — they don't stale results.)
RESULT_AFFECTING_ICP_FIELDS = (
    "campaign_objective",
    "target_industries",
    "company_size_ranges",
    "target_keywords",
    "target_geographies",
)


def icp_fingerprint(icp: ICP) -> str:
    payload = {f: getattr(icp, f) for f in RESULT_AFFECTING_ICP_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _safe_domain(website: str | None) -> str | None:
    if not website:
        return None
    try:
        return extract_domain(website)
    except Exception:
        return None


class CampaignIdentityIndex:
    """Content identity of every lead already in the campaign's permanent import."""

    def __init__(self) -> None:
        self._contacts: set[tuple[str, str]] = set()  # (company_key, contact_key)
        self._companies: set[str] = set()

    @classmethod
    def build(cls, db: Session, permanent_import_id: int) -> "CampaignIdentityIndex":
        index = cls()
        rows = db.execute(
            select(Lead, Company)
            .join(Company, Lead.company_id == Company.id)
            .where(Lead.lead_import_id == permanent_import_id)
        ).all()
        for lead, company in rows:
            ckeys = company_keys(company.name, company.domain)
            index._companies.update(ckeys)
            for ck in ckeys:
                if lead.email:
                    index._contacts.add((ck, f"e:{lead.email.lower().strip()}"))
                name_n = _norm_name(lead.full_name)
                if name_n:
                    index._contacts.add((ck, f"n:{name_n}"))
        return index

    def company_exists(self, name: str | None, domain: str | None) -> bool:
        return any(ck in self._companies for ck in company_keys(name, domain))

    def lead_exists(self, company_name: str | None, domain: str | None, email: str | None, full_name: str | None) -> bool:
        ckeys = company_keys(company_name, domain)
        for ck in ckeys:
            if email and (ck, f"e:{email.lower().strip()}") in self._contacts:
                return True
            name_n = _norm_name(full_name)
            if name_n and (ck, f"n:{name_n}") in self._contacts:
                return True
        return False


def classify_rows(db: Session, permanent_import_id: int, rows: list[dict], mapping: dict) -> dict:
    """Counts importable rows of a candidate upload as already-in-campaign vs new."""
    from app.services.csv.import_service import classify_row  # avoid circular import

    index = CampaignIdentityIndex.build(db, permanent_import_id)

    def cell(row: dict, key: str) -> str | None:
        col = mapping.get(key)
        value = (row.get(col) or "").strip() if col else ""
        return value or None

    existing = new = 0
    for row in rows:
        if classify_row(row, mapping) != "keep":
            continue
        company_name = cell(row, "company_name")
        domain = _safe_domain(cell(row, "company_website"))
        if index.lead_exists(company_name, domain, cell(row, "email"), cell(row, "full_name")):
            existing += 1
        else:
            new += 1
    return {"existing_leads": existing, "new_leads": new}


def resolve_upload_mode(db: Session, permanent: LeadImport, icp: ICP, rows: list[dict], mapping: dict) -> dict:
    """Returns {mode, new_leads, existing_leads, inputs_changed} for a pending re-upload."""
    counts = classify_rows(db, permanent.id, rows, mapping)
    inputs_changed = permanent.icp_snapshot_hash is not None and permanent.icp_snapshot_hash != icp_fingerprint(icp)
    if inputs_changed:
        mode = "rerun"
    elif counts["new_leads"] > 0:
        mode = "append"
    else:
        mode = "blocked"
    return {"mode": mode, "inputs_changed": inputs_changed, **counts}


def reset_computed_results(db: Session, permanent: LeadImport) -> None:
    """Erase every computed artifact but keep all entities and cached enrichment.
    Enrichment content/profile is preserved (input-independent + cached), so a re-run
    only re-executes the free gates and the industry/fit LLM scoring."""
    lead_ids = select(Lead.id).where(Lead.lead_import_id == permanent.id)
    db.execute(delete(EmailDraft).where(EmailDraft.lead_id.in_(lead_ids)))

    for qualification in db.scalars(
        select(CompanyQualification).where(CompanyQualification.lead_import_id == permanent.id)
    ):
        qualification.qualification_status = QualificationStatus.PENDING
        qualification.qualification_checks = None
        qualification.qualification_override = False
        qualification.industry_match_score = None
        qualification.company_fit_score = None
        qualification.qualification_reasoning = None
        qualification.solvable_pain_points = None

    for lead in db.scalars(select(Lead).where(Lead.lead_import_id == permanent.id)):
        lead.role_score = None
        lead.signal_score = None
        lead.total_score = None
        lead.tier = None
        lead.score_breakdown = None
    db.flush()
