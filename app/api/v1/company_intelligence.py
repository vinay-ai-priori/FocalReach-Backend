from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.repositories.company_intelligence_repository import CompanyIntelligenceRepository
from app.schemas.company_intelligence import CompanyIntelligenceOut

router = APIRouter(
    prefix="/company-intelligence", tags=["company-intelligence"], dependencies=[Depends(get_current_user)]
)


@router.get("/by-analysis/{analysis_id}", response_model=CompanyIntelligenceOut)
def get_by_analysis(analysis_id: int, db: Session = Depends(get_db)) -> CompanyIntelligenceOut:
    intelligence = CompanyIntelligenceRepository(db).get_by_analysis(analysis_id)
    if not intelligence:
        raise NotFoundError("Company intelligence has not been generated for this analysis yet.")
    return CompanyIntelligenceOut.model_validate(intelligence)


@router.get("/{intelligence_id}", response_model=CompanyIntelligenceOut)
def get_intelligence(intelligence_id: int, db: Session = Depends(get_db)) -> CompanyIntelligenceOut:
    intelligence = CompanyIntelligenceRepository(db).get(intelligence_id)
    if not intelligence:
        raise NotFoundError(f"Company intelligence {intelligence_id} not found.")
    return CompanyIntelligenceOut.model_validate(intelligence)
