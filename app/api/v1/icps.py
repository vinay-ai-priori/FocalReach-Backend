from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import Forbidden, get_current_user
from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.models.icp import ICP
from app.models.user import User
from app.repositories.company_intelligence_repository import CompanyIntelligenceRepository
from app.repositories.icp_repository import ICPRepository
from app.schemas.icp import ICPGenerateRequest, ICPOut, ICPUpdateRequest
from app.services.icp_service import generate_icp, update_icp

router = APIRouter(prefix="/icps", tags=["icp"])


def _get_owned_icp(icp_id: int, user: User, db: Session) -> ICP:
    icp = ICPRepository(db).get(icp_id)
    if not icp:
        raise NotFoundError(f"ICP {icp_id} not found.")
    if icp.user_id is not None and icp.user_id != user.id:
        raise Forbidden("This ICP belongs to another user's campaign.")
    return icp


@router.post("/generate", response_model=ICPOut)
def generate(
    payload: ICPGenerateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ICPOut:
    """Generate (or return the user's existing active) ICP for a company intelligence profile."""
    intelligence = CompanyIntelligenceRepository(db).get(payload.company_intelligence_id)
    if not intelligence:
        raise NotFoundError(f"Company intelligence {payload.company_intelligence_id} not found.")
    return ICPOut.model_validate(generate_icp(db, intelligence, user_id=user.id))


@router.get("/{icp_id}", response_model=ICPOut)
def get_icp(icp_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ICPOut:
    return ICPOut.model_validate(_get_owned_icp(icp_id, user, db))


@router.patch("/{icp_id}", response_model=ICPOut)
def patch_icp(
    icp_id: int, payload: ICPUpdateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ICPOut:
    _get_owned_icp(icp_id, user, db)
    return ICPOut.model_validate(update_icp(db, icp_id, payload.model_dump(exclude_unset=True)))
