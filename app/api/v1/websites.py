from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.models.website_analysis import AnalysisStatus, WebsiteAnalysis
from app.repositories.website_repository import WebsiteAnalysisRepository
from app.schemas.website import WebsiteAnalysisOut, WebsiteAnalyzeRequest
from app.services.website.url_validator import extract_domain, normalize_url, verify_reachable
from app.tasks.website_tasks import analyze_website_task

router = APIRouter(prefix="/websites", tags=["website-intelligence"], dependencies=[Depends(get_current_user)])


@router.post("/analyze", response_model=WebsiteAnalysisOut)
def analyze_website(
    payload: WebsiteAnalyzeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebsiteAnalysisOut:
    """Validate the URL with a small HTTP call, reuse the org's cached analysis when available,
    otherwise dispatch the scraping + intelligence pipeline to Celery."""
    url = normalize_url(payload.url)
    domain = extract_domain(url)
    repo = WebsiteAnalysisRepository(db)

    existing = repo.get_by_domain(domain, user.organization_id)
    if existing and not payload.force_refresh:
        if existing.status == AnalysisStatus.COMPLETED:
            out = WebsiteAnalysisOut.model_validate(existing)
            out.cached = True
            return out
        if existing.status not in (AnalysisStatus.FAILED,):
            return WebsiteAnalysisOut.model_validate(existing)  # already in flight

    final_url = verify_reachable(url)

    if existing:
        analysis = repo.update(
            existing,
            url=final_url,
            status=AnalysisStatus.PENDING,
            error_message=None,
            extracted_content=None if payload.force_refresh else existing.extracted_content,
        )
        if payload.force_refresh and analysis.company_intelligence:
            db.delete(analysis.company_intelligence)
            db.commit()
    else:
        analysis = repo.create(
            WebsiteAnalysis(
                url=final_url,
                domain=domain,
                status=AnalysisStatus.PENDING,
                organization_id=user.organization_id,
            )
        )

    analyze_website_task.delay(analysis.id)
    return WebsiteAnalysisOut.model_validate(analysis)


@router.get("/{analysis_id}", response_model=WebsiteAnalysisOut)
def get_analysis(analysis_id: int, db: Session = Depends(get_db)) -> WebsiteAnalysisOut:
    analysis = WebsiteAnalysisRepository(db).get(analysis_id)
    if not analysis:
        raise NotFoundError(f"Analysis {analysis_id} not found.")
    return WebsiteAnalysisOut.model_validate(analysis)
