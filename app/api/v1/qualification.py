from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import get_owned_import
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.models.company import Company, CompanyQualification, QualificationStatus
from app.models.user import User
from app.repositories.company_repository import CompanyRepository
from app.schemas.common import TaskAccepted
from app.schemas.company import BulkApproveRequest, CompanyOut, QualificationDecision, QualificationSummary
from app.tasks.qualification_tasks import reactivate_rejected_task
from app.tasks.scoring_tasks import score_import_task

router = APIRouter(prefix="/qualification", tags=["company-qualification"])


def _company_out(qualification: CompanyQualification, company: Company) -> CompanyOut:
    """API shape merges the canonical company with this run's qualification verdict."""
    out = CompanyOut.model_validate(company)
    out.lead_import_public_id = (
        qualification.lead_import.public_id if qualification.lead_import else None
    )
    out.qualification_status = qualification.qualification_status
    out.qualification_checks = qualification.qualification_checks
    out.qualification_override = qualification.qualification_override
    out.industry_match_score = qualification.industry_match_score
    out.company_fit_score = qualification.company_fit_score
    out.qualification_reasoning = qualification.qualification_reasoning
    return out


@router.get("/imports/{import_id}/companies", response_model=list[CompanyOut])
def list_companies(
    import_id: UUID,
    status: QualificationStatus | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CompanyOut]:
    lead_import = get_owned_import(db, import_id, user)
    pairs = CompanyRepository(db).list_for_import(lead_import.id, status)
    return [_company_out(q, c) for q, c in pairs]


@router.get("/imports/{import_id}/summary", response_model=QualificationSummary)
def summary(
    import_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> QualificationSummary:
    lead_import = get_owned_import(db, import_id, user)
    pairs = CompanyRepository(db).list_for_import(lead_import.id)
    counts = {s: 0 for s in QualificationStatus}
    for qualification, _company in pairs:
        counts[qualification.qualification_status] += 1
    return QualificationSummary(
        total=len(pairs),
        approved=counts[QualificationStatus.APPROVED],
        rejected=counts[QualificationStatus.REJECTED],
        review=counts[QualificationStatus.REVIEW],
        reactivated=counts[QualificationStatus.REACTIVATED],
        pending=counts[QualificationStatus.PENDING],
    )


@router.post("/imports/{import_id}/companies/{company_id}/decision", response_model=CompanyOut)
def decide(
    import_id: UUID,
    company_id: UUID,
    payload: QualificationDecision,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanyOut:
    """Human decision for companies in the Review bucket (or manual override of any
    bucket). Import-scoped: the decision applies to this run's verdict only, never to
    the canonical company or other campaigns."""
    lead_import = get_owned_import(db, import_id, user)
    repo = CompanyRepository(db)
    company = repo.get_by_public_id(company_id)
    if not company:
        raise NotFoundError(f"Company {company_id} not found.")
    qualification = repo.qualification_for(lead_import.id, company.id)
    if not qualification:
        raise NotFoundError(f"Company {company_id} is not part of this import.")
    if payload.status not in (QualificationStatus.APPROVED, QualificationStatus.REJECTED):
        raise ValidationFailedError("Decision must be 'approved' or 'rejected'.")
    # A manual approve out of REVIEW lands in the REACTIVATED bucket, not APPROVED —
    # the user can always tell AI verdicts and their own decisions apart.
    status = payload.status
    if status == QualificationStatus.APPROVED and qualification.qualification_status == QualificationStatus.REVIEW:
        status = QualificationStatus.REACTIVATED
    qualification.qualification_status = status
    qualification.qualification_override = True
    db.commit()
    db.refresh(qualification)
    return _company_out(qualification, company)


@router.post("/imports/{import_id}/companies/bulk-approve", response_model=QualificationSummary)
def bulk_approve(
    import_id: UUID,
    payload: BulkApproveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationSummary:
    """Reactivate a batch of REVIEW companies and immediately push their leads into
    prioritization. The companies are already enriched and scored, so no re-run is
    needed — only lead scoring, which the dispatched task recomputes idempotently."""
    if not payload.company_ids:
        raise ValidationFailedError("Select at least one company to approve.")
    lead_import = get_owned_import(db, import_id, user)
    repo = CompanyRepository(db)

    reactivated = 0
    for company_id in payload.company_ids:
        company = repo.get_by_public_id(company_id)
        qualification = repo.qualification_for(lead_import.id, company.id) if company else None
        if not qualification:
            raise NotFoundError(f"Company {company_id} is not part of this import.")
        if qualification.qualification_status != QualificationStatus.REVIEW:
            continue  # only review companies can be reactivated; skip already-decided ones
        qualification.qualification_status = QualificationStatus.REACTIVATED
        qualification.qualification_override = True
        reactivated += 1
    db.commit()

    # Score the newly included leads right away (idempotent over the whole import).
    if reactivated:
        score_import_task.delay(lead_import.id)

    return summary(import_id, user, db)


@router.post("/imports/{import_id}/companies/reactivate-rejected", response_model=TaskAccepted)
def reactivate_rejected(
    import_id: UUID,
    payload: BulkApproveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskAccepted:
    """Reactivate gate-rejected companies. Unlike REVIEW companies, these were never
    enriched or scored, so a background task runs enrichment + fit scoring before
    flipping them to REACTIVATED — their leads then join prioritization on the next
    scoring run (the page's "Proceed to Lead Prioritization" button)."""
    if not payload.company_ids:
        raise ValidationFailedError("Select at least one company to reactivate.")
    lead_import = get_owned_import(db, import_id, user)
    repo = CompanyRepository(db)

    internal_ids: list[int] = []
    for company_id in payload.company_ids:
        company = repo.get_by_public_id(company_id)
        qualification = repo.qualification_for(lead_import.id, company.id) if company else None
        if not qualification:
            raise NotFoundError(f"Company {company_id} is not part of this import.")
        if qualification.qualification_status != QualificationStatus.REJECTED:
            continue  # only rejected companies need the enrich+score path; skip others
        internal_ids.append(company.id)

    if not internal_ids:
        raise ValidationFailedError("None of the selected companies are rejected.")

    task = reactivate_rejected_task.delay(lead_import.id, internal_ids)
    return TaskAccepted(task_id=task.id, status="reactivating", resource_id=lead_import.public_id)


@router.post("/imports/{import_id}/finalize", response_model=TaskAccepted)
def finalize(import_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TaskAccepted:
    """Qualification is settled — kick off deterministic lead scoring for approved companies."""
    lead_import = get_owned_import(db, import_id, user)
    task = score_import_task.delay(lead_import.id)
    return TaskAccepted(task_id=task.id, status="scoring", resource_id=lead_import.public_id)
