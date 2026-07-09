from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import assert_import_owned, get_owned_import
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.models.email_draft import DraftStatus, EmailDraft
from app.models.lead import LeadTier
from app.repositories.email_draft_repository import EmailDraftRepository
from app.repositories.lead_repository import LeadRepository
from app.schemas.common import TaskAccepted
from app.schemas.email import DraftBatchRequest, EmailDraftOut, EmailDraftUpdate
from app.tasks.email_tasks import draft_email_task

router = APIRouter(prefix="/outreach", tags=["outreach"])


@router.post("/imports/{import_id}/draft", response_model=TaskAccepted)
def draft_batch(
    import_id: int,
    payload: DraftBatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskAccepted:
    """Queue AI email drafting. Defaults to hot + warm leads that have an email address."""
    get_owned_import(db, import_id, user)
    lead_repo = LeadRepository(db)
    draft_repo = EmailDraftRepository(db)

    if payload.lead_ids:
        leads = [lead for lead_id in payload.lead_ids if (lead := lead_repo.get(lead_id))]
    else:
        leads = [
            lead
            for lead in lead_repo.list_for_import(import_id)
            if lead.tier in (LeadTier.HOT, LeadTier.WARM)
        ]
    leads = [lead for lead in leads if lead.email]

    queued = 0
    for lead in leads:
        existing = draft_repo.get_latest_for_lead(lead.id)
        if existing and existing.status in (DraftStatus.READY, DraftStatus.GENERATING, DraftStatus.PENDING):
            continue
        draft = draft_repo.create(EmailDraft(lead_id=lead.id, status=DraftStatus.PENDING))
        draft_email_task.delay(draft.id)
        queued += 1

    return TaskAccepted(task_id=None, status=f"queued {queued} drafts", resource_id=import_id)


@router.get("/imports/{import_id}/drafts", response_model=list[EmailDraftOut])
def list_drafts(
    import_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[EmailDraftOut]:
    get_owned_import(db, import_id, user)
    return [EmailDraftOut.model_validate(d) for d in EmailDraftRepository(db).list_for_import(import_id)]


@router.get("/leads/{lead_id}/draft", response_model=EmailDraftOut)
def get_draft(lead_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> EmailDraftOut:
    draft = EmailDraftRepository(db).get_latest_for_lead(lead_id)
    if not draft:
        raise NotFoundError("No draft exists for this lead yet.")
    assert_import_owned(draft.lead.lead_import, user)
    return EmailDraftOut.model_validate(draft)


@router.patch("/drafts/{draft_id}", response_model=EmailDraftOut)
def edit_draft(
    draft_id: int, payload: EmailDraftUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    repo = EmailDraftRepository(db)
    draft = repo.get(draft_id)
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if fields:
        draft = repo.update(draft, **fields)
    return EmailDraftOut.model_validate(draft)
