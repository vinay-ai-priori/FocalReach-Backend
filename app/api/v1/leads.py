from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import assert_import_owned, get_owned_import
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.lead import LeadTier
from app.models.user import User
from app.models.lead import Lead
from app.repositories.lead_repository import LeadRepository
from app.schemas.lead import BulkReactivateRequest, LeadOut, LeadTimezoneOut, PrioritizationSummary
from app.services.lead_timezone_service import TimezoneResult, resolve_timezone_for_country

router = APIRouter(prefix="/leads", tags=["lead-prioritization"])


def _lead_out(lead: Lead) -> LeadOut:
    out = LeadOut.model_validate(lead)
    out.company_name = lead.company.name if lead.company else None
    out.lead_import_public_id = lead.lead_import.public_id if lead.lead_import else None
    out.company_public_id = lead.company.public_id if lead.company else None
    # company_fit is no longer a column — surface it from the persisted breakdown.
    breakdown = lead.score_breakdown or {}
    out.company_fit_score = (breakdown.get("company_fit") or {}).get("score")
    return out


@router.get("/imports/{import_id}", response_model=list[LeadOut])
def list_leads(
    import_id: UUID,
    tier: LeadTier | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LeadOut]:
    lead_import = get_owned_import(db, import_id, user)
    return [_lead_out(lead) for lead in LeadRepository(db).list_for_import(lead_import.id, tier)]


@router.get("/imports/{import_id}/summary", response_model=PrioritizationSummary)
def summary(
    import_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PrioritizationSummary:
    lead_import = get_owned_import(db, import_id, user)
    leads = LeadRepository(db).list_for_import(lead_import.id)
    counts = {t: 0 for t in LeadTier}
    for lead in leads:
        if lead.tier:
            counts[lead.tier] += 1
    return PrioritizationSummary(
        total=len(leads),
        hot=counts[LeadTier.HOT],
        warm=counts[LeadTier.WARM],
        nurture=counts[LeadTier.NURTURE],
        deprioritized=counts[LeadTier.DEPRIORITIZED],
        reactivated=counts[LeadTier.REACTIVATED],
    )


@router.post("/imports/{import_id}/bulk-reactivate", response_model=PrioritizationSummary)
def bulk_reactivate(
    import_id: UUID,
    payload: BulkReactivateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PrioritizationSummary:
    """Bring a batch of DEPRIORITIZED leads back into outreach. Scores are kept as-is
    (the numbers are still true) — only the tier flips to REACTIVATED, which makes the
    leads draft-eligible. Survives later re-scoring runs."""
    if not payload.lead_ids:
        raise ValidationFailedError("Select at least one lead to reactivate.")
    lead_import = get_owned_import(db, import_id, user)
    lead_repo = LeadRepository(db)

    for lead_id in payload.lead_ids:
        lead = lead_repo.get_by_public_id(lead_id)
        if not lead or lead.lead_import_id != lead_import.id:
            raise NotFoundError(f"Lead {lead_id} is not part of this import.")
        if lead.tier != LeadTier.DEPRIORITIZED:
            continue  # only deprioritized leads can be reactivated
        lead.tier = LeadTier.REACTIVATED
    db.commit()

    return summary(import_id, user, db)


@router.get("/{lead_id}/timezone", response_model=LeadTimezoneOut)
def get_lead_timezone(
    lead_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> LeadTimezoneOut:
    """Derives the lead's IANA timezone from their country column (contact location),
    used to schedule/send outreach at a sensible local time for the recipient."""
    lead_repo = LeadRepository(db)
    lead = lead_repo.get_by_public_id(lead_id)
    if not lead:
        raise NotFoundError(f"Lead {lead_id} not found.")
    assert_import_owned(lead.lead_import, user)
    if not lead.country:
        raise ValidationFailedError("This lead has no country on file — timezone cannot be derived.")

    if lead.timezone is None:
        result = resolve_timezone_for_country(lead.country)
        lead_repo.update(lead, timezone=result.timezone)
    else:
        result = TimezoneResult(country=lead.country, country_code=None, timezone=lead.timezone)

    return LeadTimezoneOut(country=result.country, country_code=result.country_code, timezone=result.timezone)


@router.post("/{lead_id}/pause", response_model=LeadOut)
def pause_lead(lead_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LeadOut:
    """Holds the whole outreach sequence for this lead — reversible via /resume, unlike
    draft approval. Doesn't touch the draft's own content or status."""
    lead_repo = LeadRepository(db)
    lead = lead_repo.get_by_public_id(lead_id)
    if not lead:
        raise NotFoundError(f"Lead {lead_id} not found.")
    assert_import_owned(lead.lead_import, user)
    lead = lead_repo.update(lead, outreach_paused=True)
    return _lead_out(lead)


@router.post("/{lead_id}/resume", response_model=LeadOut)
def resume_lead(lead_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> LeadOut:
    lead_repo = LeadRepository(db)
    lead = lead_repo.get_by_public_id(lead_id)
    if not lead:
        raise NotFoundError(f"Lead {lead_id} not found.")
    assert_import_owned(lead.lead_import, user)
    lead = lead_repo.update(lead, outreach_paused=False)
    return _lead_out(lead)
