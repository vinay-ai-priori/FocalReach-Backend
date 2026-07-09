from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth_deps import Forbidden, get_current_user
from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.models.campaign import Campaign, CampaignStatus
from app.models.user import User
from app.models.website_analysis import AnalysisStatus, WebsiteAnalysis
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.website_repository import WebsiteAnalysisRepository
from app.schemas.campaign import CampaignCreate, CampaignListOut, CampaignOut, CampaignUpdate
from app.services.campaign_service import to_out
from app.services.website.url_validator import extract_domain, normalize_url, verify_reachable
from app.tasks.website_tasks import analyze_website_task

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _get_owned(db: Session, campaign_id: int, user: User) -> Campaign:
    campaign = CampaignRepository(db).get(campaign_id)
    if not campaign:
        raise NotFoundError(f"Campaign {campaign_id} not found.")
    if campaign.user_id is not None and campaign.user_id != user.id:
        raise Forbidden("This campaign belongs to another user.")
    return campaign


@router.post("", response_model=CampaignOut)
def create_campaign(
    payload: CampaignCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CampaignOut:
    """Start a new campaign: validate + (re)use the org's website analysis, dispatch the
    scraping/intelligence pipeline, and create the campaign row."""
    url = normalize_url(payload.url)
    domain = extract_domain(url)
    repo = WebsiteAnalysisRepository(db)

    existing = repo.get_by_domain(domain, user.organization_id)
    if existing and not payload.force_refresh and existing.status != AnalysisStatus.FAILED:
        analysis = existing
    else:
        final_url = verify_reachable(url)
        if existing:
            analysis = repo.update(existing, url=final_url, status=AnalysisStatus.PENDING, error_message=None)
        else:
            analysis = repo.create(
                WebsiteAnalysis(
                    url=final_url, domain=domain, status=AnalysisStatus.PENDING, organization_id=user.organization_id
                )
            )
        analyze_website_task.delay(analysis.id)

    campaign = CampaignRepository(db).create(
        Campaign(
            user_id=user.id,
            organization_id=user.organization_id,
            status=CampaignStatus.ACTIVE,
            website_analysis_id=analysis.id,
        )
    )
    return to_out(campaign)


@router.get("", response_model=CampaignListOut)
def list_campaigns(
    status: CampaignStatus = Query(CampaignStatus.ACTIVE),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignListOut:
    items, total = CampaignRepository(db).list_for_user(user.id, status, page, page_size)
    return CampaignListOut(items=[to_out(c) for c in items], total=total, page=page, page_size=page_size)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CampaignOut:
    return to_out(_get_owned(db, campaign_id, user))


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: int, payload: CampaignUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CampaignOut:
    campaign = _get_owned(db, campaign_id, user)
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if fields:
        campaign = CampaignRepository(db).update(campaign, **fields)
    return to_out(campaign)


@router.delete("/{campaign_id}", response_model=CampaignOut)
def delete_campaign(
    campaign_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CampaignOut:
    """Delete the campaign aggregate. Its leads stay in the DB for org-level dedup."""
    campaign = _get_owned(db, campaign_id, user)
    out = to_out(campaign)
    CampaignRepository(db).delete(campaign)
    return out
