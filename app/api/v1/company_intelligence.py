from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.repositories.company_intelligence_repository import CompanyIntelligenceRepository
from app.repositories.website_repository import WebsiteAnalysisRepository
from app.schemas.company_intelligence import CompanyIntelligenceOut

router = APIRouter(
    prefix="/company-intelligence", tags=["company-intelligence"], dependencies=[Depends(get_current_user)]
)


def _intelligence_out(intelligence) -> CompanyIntelligenceOut:
    out = CompanyIntelligenceOut.model_validate(intelligence)
    out.website_analysis_public_id = intelligence.website_analysis.public_id if intelligence.website_analysis else None
    return out


@router.get("/by-analysis/{analysis_id}", response_model=CompanyIntelligenceOut)
def get_by_analysis(analysis_id: UUID, db: Session = Depends(get_db)) -> CompanyIntelligenceOut:
    analysis = WebsiteAnalysisRepository(db).get_by_public_id(analysis_id)
    intelligence = CompanyIntelligenceRepository(db).get_by_analysis(analysis.id) if analysis else None
    if not intelligence:
        raise NotFoundError("Company intelligence has not been generated for this analysis yet.")
    return _intelligence_out(intelligence)


@router.get("/{intelligence_id}", response_model=CompanyIntelligenceOut)
def get_intelligence(intelligence_id: UUID, db: Session = Depends(get_db)) -> CompanyIntelligenceOut:
    intelligence = CompanyIntelligenceRepository(db).get_by_public_id(intelligence_id)
    if not intelligence:
        raise NotFoundError(f"Company intelligence {intelligence_id} not found.")
    return _intelligence_out(intelligence)
