from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
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


def _draft_out(draft: EmailDraft) -> EmailDraftOut:
    out = EmailDraftOut.model_validate(draft)
    out.lead_public_id = draft.lead.public_id if draft.lead else None
    return out


@router.post("/imports/{import_id}/draft", response_model=TaskAccepted)
def draft_batch(
    import_id: UUID,
    payload: DraftBatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskAccepted:
    """Queue AI email drafting. Defaults to all leads except deprioritized ones."""
    lead_import = get_owned_import(db, import_id, user)
    lead_repo = LeadRepository(db)
    draft_repo = EmailDraftRepository(db)

    if payload.lead_ids:
        leads = [lead for lead_public_id in payload.lead_ids if (lead := lead_repo.get_by_public_id(lead_public_id))]
    else:
        leads = [
            lead
            for lead in lead_repo.list_for_import(lead_import.id)
            if lead.tier in (LeadTier.HOT, LeadTier.WARM, LeadTier.NURTURE)
        ]
    leads = [lead for lead in leads if lead.email]

    # Batch-commit drafts (10 per commit) instead of one commit per lead: fewer DB
    # round-trips and at most one small batch of unpersisted rows held in the session.
    # Celery tasks are queued only AFTER their batch commits, so a worker never picks
    # up a draft id that isn't in the DB yet.
    queued = 0
    batch: list[EmailDraft] = []

    def _commit_batch() -> int:
        nonlocal batch
        if not batch:
            return 0
        try:
            db.add_all(batch)
            db.flush()  # assigns ids now, so reading them later needs no per-row refresh
            draft_ids = [item.id for item in batch]
            db.commit()
        except IntegrityError:
            # Lost a race with a concurrent request on one or more leads (enforced by
            # the ux_email_drafts_lead_active partial index). Retry each draft alone so
            # only the conflicting ones are dropped.
            db.rollback()
            committed = 0
            for item in batch:
                retry = EmailDraft(lead_id=item.lead_id, status=DraftStatus.PENDING)
                try:
                    db.add(retry)
                    db.flush()
                    retry_id = retry.id
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    continue
                draft_email_task.delay(retry_id)
                committed += 1
            batch = []
            return committed
        for draft_id in draft_ids:
            draft_email_task.delay(draft_id)
        committed = len(draft_ids)
        batch = []
        return committed

    for lead in leads:
        existing = draft_repo.get_latest_for_lead(lead.id)
        if existing and existing.status in (DraftStatus.READY, DraftStatus.GENERATING, DraftStatus.PENDING):
            continue
        batch.append(EmailDraft(lead_id=lead.id, status=DraftStatus.PENDING))
        if len(batch) >= 10:
            queued += _commit_batch()
    queued += _commit_batch()

    return TaskAccepted(task_id=None, status=f"queued {queued} drafts", resource_id=lead_import.public_id)


@router.get("/imports/{import_id}/drafts", response_model=list[EmailDraftOut])
def list_drafts(
    import_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[EmailDraftOut]:
    lead_import = get_owned_import(db, import_id, user)
    return [_draft_out(d) for d in EmailDraftRepository(db).list_for_import(lead_import.id)]


@router.get("/leads/{lead_id}/draft", response_model=EmailDraftOut)
def get_draft(lead_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> EmailDraftOut:
    lead = LeadRepository(db).get_by_public_id(lead_id)
    if not lead:
        raise NotFoundError("No draft exists for this lead yet.")
    draft = EmailDraftRepository(db).get_latest_for_lead(lead.id)
    if not draft:
        raise NotFoundError("No draft exists for this lead yet.")
    assert_import_owned(draft.lead.lead_import, user)
    return _draft_out(draft)


@router.patch("/drafts/{draft_id}", response_model=EmailDraftOut)
def edit_draft(
    draft_id: UUID, payload: EmailDraftUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    repo = EmailDraftRepository(db)
    draft = repo.get_by_public_id(draft_id)
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if fields:
        draft = repo.update(draft, **fields)
    return _draft_out(draft)
