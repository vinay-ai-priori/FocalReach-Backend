from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.api.ownership import assert_import_owned, get_owned_import
from app.core.crypto import decrypt_secret
from app.core.exceptions import AppException, ConflictError, NotFoundError, ValidationFailedError
from app.models.user import User
from app.models.email_draft import REFINE_LIMIT, DraftStatus, EmailDraft
from app.models.lead import LeadTier
from app.repositories.email_draft_repository import EmailDraftRepository
from app.repositories.lead_repository import LeadRepository
from app.repositories.mailbox_repository import MailboxConnectionRepository
from app.schemas.common import TaskAccepted
from app.schemas.email import (
    DraftBatchRequest,
    DraftRefineRequest,
    EmailDraftOut,
    EmailDraftUpdate,
    SendTestRequest,
)
from app.services.mailbox.connection_service import send_email_via_smtp
from app.services.mailbox.providers import get_preset
from app.tasks.email_tasks import draft_email_task

TEST_SEND_COOLDOWN = timedelta(seconds=5)

router = APIRouter(prefix="/outreach", tags=["outreach"])


def _draft_out(draft: EmailDraft) -> EmailDraftOut:
    out = EmailDraftOut.model_validate(draft)
    out.lead_public_id = draft.lead.public_id if draft.lead else None
    return out


def _assert_not_paused(draft: EmailDraft) -> None:
    if draft.lead.outreach_paused:
        raise ValidationFailedError("Outreach is paused for this lead — resume it first.")


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


@router.post("/drafts/{draft_id}/refine", response_model=EmailDraftOut)
def refine_draft(
    draft_id: UUID, payload: DraftRefineRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Queue a regenerate/refine pass on an existing draft. The rewrite happens in
    Celery; the draft flips to GENERATING immediately and the UI's polling picks up
    the READY result.

    Row-locked so two rapid clicks can't both read refine_count before either writes
    it back — the second waits for the lock, then sees the first's incremented count
    and is correctly rejected once the limit is hit."""
    draft = db.scalars(select(EmailDraft).where(EmailDraft.public_id == draft_id).with_for_update()).first()
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    _assert_not_paused(draft)
    if draft.status in (DraftStatus.PENDING, DraftStatus.GENERATING):
        raise ValidationFailedError("This draft is still being generated — try again once it's ready.")
    if draft.status == DraftStatus.APPROVED:
        raise ValidationFailedError("This email has been approved and can no longer be edited.")
    if draft.refine_count >= REFINE_LIMIT:
        raise ValidationFailedError(
            f"This email has reached its limit of {REFINE_LIMIT} rewrites "
            "(Regenerate, Shorter, More Technical, More Executive, More Friendly, Personalize Further "
            "all share the same limit)."
        )
    draft.status = DraftStatus.PENDING
    draft.error_message = None
    draft.refine_count += 1
    db.commit()
    draft_email_task.delay(draft.id, payload.mode)
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
    if draft.status == DraftStatus.APPROVED:
        raise ValidationFailedError("This email has been approved and can no longer be edited.")
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if fields:
        draft = repo.update(draft, **fields)
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/approve", response_model=EmailDraftOut)
def approve_draft(
    draft_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Locks in a ready draft: once approved, it can no longer be edited/regenerated/
    refined (enforced here and mirrored in the UI, which drops those controls). One-way
    for now — no unapprove path."""
    repo = EmailDraftRepository(db)
    draft = repo.get_by_public_id(draft_id)
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    if draft.status == DraftStatus.APPROVED:
        return _draft_out(draft)  # already approved — idempotent, so a duplicate click is a no-op
    _assert_not_paused(draft)
    if draft.status != DraftStatus.READY:
        raise ValidationFailedError("Only a ready draft can be approved.")
    draft = repo.update(draft, status=DraftStatus.APPROVED)
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/send", response_model=EmailDraftOut)
def send_draft(
    draft_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Sends a ready draft through the user's own connected mailbox (SMTP), then
    marks it SENT. Requires a mailbox connection and a lead with an email address."""
    repo = EmailDraftRepository(db)
    draft = repo.get_by_public_id(draft_id)
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    _assert_not_paused(draft)

    if draft.status not in (DraftStatus.READY, DraftStatus.APPROVED):
        raise ValidationFailedError("Only a ready (or approved) draft can be sent.")
    if not draft.subject or not draft.body:
        raise ValidationFailedError("This draft has no subject/body to send.")
    if not draft.lead.email:
        raise ValidationFailedError("This lead has no email address on file.")

    mailboxes = MailboxConnectionRepository(db).list_for_user(user.id)
    mailbox = next((m for m in mailboxes if m.is_connected), None)
    if not mailbox:
        raise ValidationFailedError("Connect a mailbox before sending outreach.")

    preset = get_preset(mailbox.provider)
    app_password = decrypt_secret(mailbox.encrypted_app_password)
    try:
        send_email_via_smtp(
            preset,
            mailbox.email_address,
            app_password,
            to=draft.lead.email,
            subject=draft.subject,
            body=draft.body,
        )
    except AppException as exc:
        # Keep the draft READY (not FAILED — that status means AI generation
        # failed) so the user can just retry sending, but persist the reason so
        # it's visible on the draft, not just a one-off toast.
        repo.update(draft, error_message=str(exc.message)[:1000])
        raise

    draft = repo.update(draft, status=DraftStatus.SENT, error_message=None, sent_at=datetime.now(timezone.utc))
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/send-test", response_model=EmailDraftOut)
def send_test(
    draft_id: UUID, payload: SendTestRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Sends the current draft content to the given address immediately. Treated the same
    as the real send (flips status to SENT, stamps sent_at) since it dispatches an actual
    email to a real inbox — not a dry-run preview. Also persists that address onto the
    lead's email so a typo caught in the test popup is fixed for the record.

    Row-locks the draft for the duration of the request and enforces a short cooldown on
    top of it, so a double-click (or two tabs) can't fire two test emails for one draft:
    the second request blocks on the lock, then sees the first request's fresh
    last_test_sent_at and is rejected before it ever calls SMTP.
    """
    repo = EmailDraftRepository(db)
    draft = db.scalars(select(EmailDraft).where(EmailDraft.public_id == draft_id).with_for_update()).first()
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    _assert_not_paused(draft)

    if draft.status not in (DraftStatus.READY, DraftStatus.APPROVED):
        raise ValidationFailedError("Only a ready (or approved) draft can be sent.")

    now = datetime.now(timezone.utc)
    if draft.last_test_sent_at and now - draft.last_test_sent_at < TEST_SEND_COOLDOWN:
        raise ConflictError("A test email was just sent for this draft — please wait a moment and try again.")

    if not draft.subject or not draft.body:
        raise ValidationFailedError("This draft has no subject/body to send.")

    mailboxes = MailboxConnectionRepository(db).list_for_user(user.id)
    mailbox = next((m for m in mailboxes if m.is_connected), None)
    if not mailbox:
        raise ValidationFailedError("Connect a mailbox before sending a test.")

    # Claim the cooldown window before dispatching, still inside the row lock, so a
    # concurrent request that was waiting on the lock sees it the instant we commit.
    repo.update(draft, last_test_sent_at=now)

    preset = get_preset(mailbox.provider)
    app_password = decrypt_secret(mailbox.encrypted_app_password)
    try:
        send_email_via_smtp(
            preset,
            mailbox.email_address,
            app_password,
            to=payload.email,
            subject=draft.subject,
            body=draft.body,
        )
    except AppException as exc:
        repo.update(draft, error_message=str(exc.message)[:1000])
        raise

    LeadRepository(db).update(draft.lead, email=payload.email)
    draft = repo.update(
        draft,
        last_test_email=payload.email,
        error_message=None,
        status=DraftStatus.SENT,
        sent_at=now,
    )
    return _draft_out(draft)
