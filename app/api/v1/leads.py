from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import get_owned_import
from app.models.lead import LeadTier
from app.models.user import User
from app.models.lead import Lead
from app.repositories.lead_repository import LeadRepository
from app.schemas.lead import LeadOut, PrioritizationSummary

router = APIRouter(prefix="/leads", tags=["lead-prioritization"])


def _lead_out(lead: Lead) -> LeadOut:
    out = LeadOut.model_validate(lead)
    out.company_name = lead.company.name if lead.company else None
    return out


@router.get("/imports/{import_id}", response_model=list[LeadOut])
def list_leads(
    import_id: int,
    tier: LeadTier | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LeadOut]:
    get_owned_import(db, import_id, user)
    return [_lead_out(lead) for lead in LeadRepository(db).list_for_import(import_id, tier)]


@router.get("/imports/{import_id}/summary", response_model=PrioritizationSummary)
def summary(
    import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PrioritizationSummary:
    get_owned_import(db, import_id, user)
    leads = LeadRepository(db).list_for_import(import_id)
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
    )
