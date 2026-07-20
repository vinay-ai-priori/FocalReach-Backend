"""Cross-campaign lead deduplication (deterministic — no AI).

Scope is the ORGANIZATION: an incoming lead is checked against every campaign belonging
to any member of the same organization, not just the uploader's own campaigns.

Rule (careful, lead-level, gated by company):
  1. Company gate — an incoming company must already be targeted in another of the owner's
     campaigns, matched by website DOMAIN first, then normalized NAME.
  2. Lead match — only inside an already-targeted company, match the contact by EMAIL first,
     then normalized full NAME.
  3. Eliminate only if the matched existing lead is ACTIVE (tier != deprioritized). If the
     existing lead was deprioritized, the incoming lead is kept (fair to re-target).
"""

import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.company import Company
from app.models.lead import Lead, LeadTier
from app.models.lead_import import ImportKind, LeadImport
from app.models.user import User
from app.services.website.url_validator import extract_domain

_LEGAL_SUFFIXES = (
    r"\b(inc|incorporated|llc|ltd|limited|gmbh|corp|corporation|co|company|plc|kk|ag|sa|bv|srl|group|holdings?)\b"
)


def _norm_name(value: str | None) -> str:
    if not value:
        return ""
    v = value.lower().strip()
    v = re.sub(r"[.,&]", " ", v)
    v = re.sub(_LEGAL_SUFFIXES, " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def _safe_domain(website: str | None) -> str | None:
    if not website:
        return None
    try:
        return extract_domain(website)
    except Exception:
        return None


def company_keys(name: str | None, domain: str | None) -> set[str]:
    keys: set[str] = set()
    if domain:
        keys.add(f"d:{domain.lower().strip()}")
    normalized = _norm_name(name)
    if normalized:
        keys.add(f"n:{normalized}")
    return keys


def _representative_company_key(name: str | None, domain: str | None) -> str | None:
    if domain:
        return f"d:{domain.lower().strip()}"
    normalized = _norm_name(name)
    return f"n:{normalized}" if normalized else None


class DedupIndex:
    """Prebuilt lookup of the owner's existing (other-campaign) leads."""

    def __init__(self) -> None:
        self.targeted_companies: set[str] = set()
        self._email_active: dict[tuple[str, str], bool] = {}
        self._name_active: dict[tuple[str, str], bool] = {}

    def add_existing(self, ckeys: set[str], email: str | None, full_name: str | None, active: bool) -> None:
        email_l = email.lower().strip() if email else ""
        name_n = _norm_name(full_name)
        for ck in ckeys:
            self.targeted_companies.add(ck)
            if email_l:
                key = (ck, email_l)
                self._email_active[key] = self._email_active.get(key, False) or active
            if name_n:
                key = (ck, name_n)
                self._name_active[key] = self._name_active.get(key, False) or active

    def evaluate(
        self, company_name: str | None, domain: str | None, email: str | None, full_name: str | None
    ) -> tuple[bool, str | None, bool]:
        """Returns (is_duplicate, reason, company_already_targeted)."""
        ckeys = company_keys(company_name, domain)
        company_targeted = any(ck in self.targeted_companies for ck in ckeys)
        if not company_targeted:
            return False, None, False

        email_l = email.lower().strip() if email else ""
        name_n = _norm_name(full_name)
        for ck in ckeys:
            if email_l and self._email_active.get((ck, email_l)) is True:
                return True, "Already active in another campaign in your organization (email match)", True
            if name_n and self._name_active.get((ck, name_n)) is True:
                return True, "Already active in another campaign in your organization (name match)", True
        # Company overlaps, but this specific contact is new (or the existing one is deprioritized).
        return False, None, True


def build_dedup_index(
    db: Session, organization_id: int | None, exclude_import_ids: set[int] | int | None
) -> DedupIndex:
    """`exclude_import_ids` must contain the import being validated AND the campaign's own
    permanent import (when re-uploading) — a campaign must never dedup against itself."""
    index = DedupIndex()
    if isinstance(exclude_import_ids, int):
        exclude_import_ids = {exclude_import_ids}
    excluded = {i for i in (exclude_import_ids or set()) if i is not None}
    # NULL organization = the super admin's own space; dedup still applies within it.
    org_filter = (
        User.organization_id.is_(None) if organization_id is None else User.organization_id == organization_id
    )
    stmt = (
        select(Lead, Company)
        .join(Company, Lead.company_id == Company.id)
        .join(LeadImport, Lead.lead_import_id == LeadImport.id)
        .join(Campaign, LeadImport.campaign_id == Campaign.id)
        .join(User, Campaign.user_id == User.id)
        .where(org_filter, Lead.is_duplicate.is_(False))
    )
    if excluded:
        stmt = stmt.where(LeadImport.id.not_in(excluded))

    for lead, company in db.execute(stmt).all():
        active = lead.tier != LeadTier.DEPRIORITIZED  # unscored (tier None) counts as active
        index.add_existing(company_keys(company.name, company.domain), lead.email, lead.full_name, active)
    return index


# Preview stats (compute_dedup_stats) re-run on every column-mapping edit while the user is
# still on the upload/mapping screen — often several times in a row. Rebuilding the index means
# re-querying every Lead+Company the organization has ever targeted, which dominates request time
# for orgs with any real history. The index only depends on (organization_id, excluded import ids),
# not on the mapping, so it's safe to cache briefly across those repeated edits. Confirm-time
# dedup (import_service.py) calls build_dedup_index directly and is never served from this cache,
# since that decision must always see the latest data.
_DEDUP_INDEX_CACHE_TTL_SECONDS = 30
_dedup_index_cache: dict[tuple, tuple[float, "DedupIndex"]] = {}


def _cached_dedup_index(db: Session, organization_id: int | None, excluded: set[int]) -> "DedupIndex":
    key = (organization_id, frozenset(excluded))
    now = time.monotonic()
    cached = _dedup_index_cache.get(key)
    if cached is not None and now - cached[0] < _DEDUP_INDEX_CACHE_TTL_SECONDS:
        return cached[1]
    index = build_dedup_index(db, organization_id, excluded)
    _dedup_index_cache[key] = (now, index)
    return index


def compute_dedup_stats(db: Session, lead_import: LeadImport, rows: list[dict] | None = None) -> dict:
    """Analysis for the upload page, computed from the raw rows vs the organization's existing leads.
    Only rows that would actually be imported (see classify_row) are considered."""
    from app.services.csv.import_service import classify_row

    from app.services.csv.import_service import organization_id_of, raw_rows_of

    excluded = {lead_import.id}
    if lead_import.kind == ImportKind.PENDING_REUPLOAD:
        # Pending re-upload: also exclude the campaign's own permanent import.
        permanent = db.scalars(
            select(LeadImport).where(
                LeadImport.campaign_id == lead_import.campaign_id,
                LeadImport.kind == ImportKind.PRIMARY,
            )
        ).first()
        if permanent:
            excluded.add(permanent.id)
    index = _cached_dedup_index(db, organization_id_of(lead_import), excluded)
    mapping = lead_import.column_mapping or {}
    if rows is None:
        rows = raw_rows_of(db, lead_import)

    def cell(row: dict, key: str) -> str:
        col = mapping.get(key)
        return (row.get(col) or "").strip() if col else ""

    targeted: set[str] = set()
    duplicate_leads = 0
    total_leads = 0

    for row in rows:
        if classify_row(row, mapping) != "keep":
            continue  # dropped rows never become leads
        total_leads += 1
        full_name = cell(row, "full_name")
        company_name = cell(row, "company_name")
        domain = _safe_domain(cell(row, "company_website"))
        email = cell(row, "email")
        is_dup, _reason, company_targeted = index.evaluate(company_name, domain, email, full_name)
        if company_targeted:
            rep = _representative_company_key(company_name, domain)
            if rep:
                targeted.add(rep)
        if is_dup:
            duplicate_leads += 1

    return {
        "already_targeted_companies": len(targeted),
        "duplicate_active_leads": duplicate_leads,
        "net_new_leads": total_leads - duplicate_leads,
    }
