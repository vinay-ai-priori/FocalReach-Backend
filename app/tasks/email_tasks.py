from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.email_draft import DraftStatus, EmailDraft
from app.repositories.company_intelligence_repository import CompanyIntelligenceRepository
from app.repositories.email_draft_repository import EmailDraftRepository
from app.repositories.lead_repository import LeadRepository
from app.services.campaign_service import active_icp_of
from app.repositories.user_repository import UserRepository
from app.services.email_service import generate_email_draft

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="email.draft", bind=True, max_retries=1, rate_limit="30/m")
def draft_email_task(self, draft_id: int, mode: str = "initial") -> dict:
    db = SessionLocal()
    try:
        draft_repo = EmailDraftRepository(db)
        draft = draft_repo.get(draft_id)
        if not draft:
            return {"draft_id": draft_id, "skipped": True}
        # Only the first generation skips READY drafts; regenerate/refine explicitly
        # rewrite a READY draft in place.
        if mode == "initial" and draft.status == DraftStatus.READY:
            return {"draft_id": draft_id, "skipped": True}

        lead = LeadRepository(db).get(draft.lead_id)
        company = lead.company
        campaign = lead.lead_import.campaign
        icp = active_icp_of(campaign)
        intelligence = (
            CompanyIntelligenceRepository(db).get(icp.company_intelligence_id)
            if icp and icp.company_intelligence_id
            else None
        )
        sender = UserRepository(db).get(campaign.user_id)

        generate_email_draft(db, draft, lead, company, icp, intelligence, sender=sender, mode=mode)
        return {"draft_id": draft.id, "status": draft.status.value, "mode": mode}
    finally:
        db.close()
