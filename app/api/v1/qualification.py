from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import Forbidden, get_current_user
from app.api.deps import get_db
from app.api.ownership import assert_import_owned, get_owned_import
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.company import QualificationStatus
from app.models.user import User
from app.repositories.company_repository import CompanyRepository
from app.schemas.common import TaskAccepted
from app.schemas.company import CompanyOut, QualificationDecision, QualificationSummary
from app.tasks.scoring_tasks import score_import_task

router = APIRouter(prefix="/qualification", tags=["company-qualification"])


@router.get("/imports/{import_id}/companies", response_model=list[CompanyOut])
def list_companies(
    import_id: int,
    status: QualificationStatus | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CompanyOut]:
    get_owned_import(db, import_id, user)
    companies = CompanyRepository(db).list_for_import(import_id, status)
    return [CompanyOut.model_validate(c) for c in companies]


@router.get("/imports/{import_id}/summary", response_model=QualificationSummary)
def summary(
    import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> QualificationSummary:
    get_owned_import(db, import_id, user)
    companies = CompanyRepository(db).list_for_import(import_id)
    counts = {s: 0 for s in QualificationStatus}
    for company in companies:
        counts[company.qualification_status] += 1
    return QualificationSummary(
        total=len(companies),
        approved=counts[QualificationStatus.APPROVED],
        rejected=counts[QualificationStatus.REJECTED],
        review=counts[QualificationStatus.REVIEW],
        pending=counts[QualificationStatus.PENDING],
    )


@router.post("/companies/{company_id}/decision", response_model=CompanyOut)
def decide(
    company_id: int,
    payload: QualificationDecision,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanyOut:
    """Human decision for companies in the Review bucket (or manual override of any bucket)."""
    repo = CompanyRepository(db)
    company = repo.get(company_id)
    if not company:
        raise NotFoundError(f"Company {company_id} not found.")
    assert_import_owned(company.lead_import, user)
    if payload.status not in (QualificationStatus.APPROVED, QualificationStatus.REJECTED):
        raise ValidationFailedError("Decision must be 'approved' or 'rejected'.")
    company = repo.update(company, qualification_status=payload.status, qualification_override=True)
    return CompanyOut.model_validate(company)


@router.post("/imports/{import_id}/finalize", response_model=TaskAccepted)
def finalize(import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskAccepted:
    """Qualification is settled — kick off deterministic lead scoring for approved companies."""
    get_owned_import(db, import_id, user)
    task = score_import_task.delay(import_id)
    return TaskAccepted(task_id=task.id, status="scoring", resource_id=import_id)
