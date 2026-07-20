from datetime import datetime, timedelta, timezone
from email.utils import make_msgid
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
from app.models.email_draft import (
    REFINE_LIMIT,
    STEP_CALL,
    STEP_FOLLOW_UP_FIRST,
    STEP_FOLLOW_UP_LAST,
    STEP_INITIAL,
    STEP_LINKEDIN,
    DispatchLog,
    DraftChannel,
    DraftStatus,
    EmailDraft,
)
from app.models.notification import Notification
from app.models.lead import LeadTier
from app.repositories.email_draft_repository import EmailDraftRepository
from app.repositories.lead_repository import LeadRepository
from app.repositories.mailbox_repository import MailboxConnectionRepository
from app.schemas.common import TaskAccepted
from app.schemas.email import (
    DispatchResolveRequest,
    DraftBatchRequest,
    DraftRefineRequest,
    EmailDraftOut,
    EmailDraftUpdate,
    SendTestRequest,
    StepCreateRequest,
)
from app.services import scheduling_service as scheduling
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
            if lead.tier in (LeadTier.HOT, LeadTier.WARM, LeadTier.NURTURE, LeadTier.REACTIVATED)
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


_DISPATCHED = (DraftStatus.SENT, DraftStatus.SCHEDULED, DraftStatus.SENDING, DraftStatus.NEEDS_ATTENTION)
_COMPLETED_STEP = (DraftStatus.SENT, DraftStatus.APPROVED)  # unlocks the next step


def _owned_lead(db: Session, lead_id: UUID, user: User):
    lead = LeadRepository(db).get_by_public_id(lead_id)
    if not lead:
        raise NotFoundError(f"Lead {lead_id} not found.")
    assert_import_owned(lead.lead_import, user)
    return lead


@router.get("/leads/{lead_id}/steps", response_model=list[EmailDraftOut])
def list_steps(lead_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[EmailDraftOut]:
    """The lead's full outreach sequence: initial email, follow-ups, LinkedIn, call."""
    lead = _owned_lead(db, lead_id, user)
    return [_draft_out(d) for d in EmailDraftRepository(db).list_for_lead(lead.id)]


@router.post("/leads/{lead_id}/steps", response_model=EmailDraftOut)
def create_step(
    lead_id: UUID,
    payload: StepCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailDraftOut:
    """Generate the next outreach step on user click — nothing in the sequence beyond
    the initial email is ever created automatically.

    Gating: follow-ups (2-4) are strictly sequential and unlock once the previous email
    step is sent or approved; LinkedIn and the call script unlock as soon as the initial
    email is sent/approved, independent of follow-up progress. A FAILED draft in the
    target slot is retried in place rather than duplicated."""
    lead = _owned_lead(db, lead_id, user)
    if lead.outreach_paused:
        raise ValidationFailedError("Outreach is paused for this lead — resume it first.")
    if payload.channel == DraftChannel.EMAIL and not lead.email:
        raise ValidationFailedError("This lead has no email address on file.")
    repo = EmailDraftRepository(db)

    # Everything after step 1 is gated on the initial email being out the door.
    initial = repo.get_step(lead.id, DraftChannel.EMAIL, STEP_INITIAL)
    if not initial or initial.status not in _COMPLETED_STEP:
        raise ValidationFailedError("Send the initial email first — later touches build on it.")

    if payload.channel == DraftChannel.EMAIL:
        # Next free follow-up slot, gated on the previous email step being sent/approved.
        target = None
        for idx in range(STEP_FOLLOW_UP_FIRST, STEP_FOLLOW_UP_LAST + 1):
            existing = repo.get_step(lead.id, DraftChannel.EMAIL, idx)
            if existing and existing.status != DraftStatus.FAILED:
                continue
            previous = initial if idx == STEP_FOLLOW_UP_FIRST else repo.get_step(lead.id, DraftChannel.EMAIL, idx - 1)
            if not previous or previous.status not in _COMPLETED_STEP:
                raise ValidationFailedError(
                    "The previous email in the sequence must be sent (or approved) before drafting the next follow-up."
                )
            target = (idx, existing)
            break
        if target is None:
            raise ValidationFailedError("All three follow-ups have already been drafted for this lead.")
        step_index, failed = target
    else:
        step_index = STEP_LINKEDIN if payload.channel == DraftChannel.LINKEDIN else STEP_CALL
        failed = repo.get_step(lead.id, payload.channel, step_index)
        if failed and failed.status != DraftStatus.FAILED:
            raise ValidationFailedError("This step has already been generated for this lead.")

    if failed:  # retry the failed generation in place
        failed.status = DraftStatus.PENDING
        failed.error_message = None
        draft = failed
    else:
        draft = EmailDraft(lead_id=lead.id, channel=payload.channel, step_index=step_index, status=DraftStatus.PENDING)
        db.add(draft)
    try:
        db.flush()
        draft_id = draft.id
        db.commit()
    except IntegrityError:
        # ux_email_drafts_step_active: a concurrent request beat us to this slot.
        db.rollback()
        raise ConflictError("This step is already being generated — refresh to see it.")
    draft_email_task.delay(draft_id)

    # Drafting the step resolves any pending follow-up-due nudge for this lead.
    db.query(Notification).filter(
        Notification.lead_id == lead.id, Notification.read_at.is_(None)
    ).update({"read_at": datetime.now(timezone.utc)})
    db.commit()
    return _draft_out(repo.get(draft_id))


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
    if draft.status in (DraftStatus.SCHEDULED, DraftStatus.SENDING, DraftStatus.SENT, DraftStatus.NEEDS_ATTENTION):
        raise ValidationFailedError("This email is scheduled or already dispatched and can no longer be refined.")
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
    if draft.status in (DraftStatus.SCHEDULED, DraftStatus.SENDING, DraftStatus.SENT, DraftStatus.NEEDS_ATTENTION):
        raise ValidationFailedError(
            "This email is scheduled or already dispatched and can no longer be edited — cancel the schedule first."
        )
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


def _get_dispatchable_draft(db: Session, draft_id: UUID, user: User) -> EmailDraft:
    """Row-locked draft + the shared guard stack for send/schedule. The row lock plus
    the per-user advisory slot lock make double-clicks and Send-vs-Schedule races
    impossible: the second request waits, then sees the first's committed status."""
    draft = db.scalars(select(EmailDraft).where(EmailDraft.public_id == draft_id).with_for_update()).first()
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    _assert_not_paused(draft)

    if draft.channel != DraftChannel.EMAIL:
        raise ValidationFailedError("LinkedIn messages and call scripts are drafts only — they are never dispatched.")
    if draft.status == DraftStatus.SCHEDULED:
        raise ConflictError("This email is already scheduled — cancel the schedule first to change it.")
    if draft.status in (DraftStatus.SENDING, DraftStatus.SENT):
        raise ConflictError("This email has already been dispatched.")
    if draft.status == DraftStatus.NEEDS_ATTENTION:
        raise ConflictError(
            "A previous dispatch of this email was interrupted — resolve it (retry or mark as sent) first."
        )
    if draft.status not in (DraftStatus.READY, DraftStatus.APPROVED):
        raise ValidationFailedError("Only a ready (or approved) draft can be sent.")
    if not draft.subject or not draft.body:
        raise ValidationFailedError("This draft has no subject/body to send.")
    if not draft.lead.email:
        raise ValidationFailedError("This lead has no email address on file.")
    return draft


def _connected_mailbox(db: Session, user: User):
    mailboxes = MailboxConnectionRepository(db).list_for_user(user.id)
    mailbox = next((m for m in mailboxes if m.is_connected), None)
    if not mailbox:
        raise ValidationFailedError("Connect a mailbox before sending outreach.")
    return mailbox


def _log_dispatch(db: Session, draft: EmailDraft, outcome: str, detail: str | None = None) -> None:
    db.add(
        DispatchLog(
            draft_id=draft.id,
            attempt=draft.attempt_count,
            scheduled_for=draft.scheduled_at,
            outcome=outcome,
            detail=(detail or "")[:1024] or None,
            message_id=draft.message_id,
        )
    )


@router.post("/drafts/{draft_id}/schedule", response_model=EmailDraftOut)
def schedule_draft(
    draft_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Books the next valid dispatch slot: inside Mon-Fri 9-12/13-16 in the LEAD's
    timezone (derived now: cached -> country -> UTC) and at least 2 minutes away from
    every other dispatch for this user. 'Now' inside business hours books now+1min."""
    draft = _get_dispatchable_draft(db, draft_id, user)
    _connected_mailbox(db, user)  # fail fast; re-checked again at fire time

    lead_tz = scheduling.resolve_lead_timezone(db, draft.lead)
    scheduling.acquire_user_schedule_lock(db, user.id)
    now = scheduling.db_now(db)
    slot = scheduling.allocate_scheduled_slot(
        db, user.id, lead_tz, now + scheduling.IMMEDIATE_DELAY, exclude_draft_id=draft.id
    )

    draft.status = DraftStatus.SCHEDULED
    draft.scheduled_at = slot
    draft.scheduled_by_user_id = user.id
    draft.attempt_count = 0
    draft.error_message = None
    _log_dispatch(db, draft, "scheduled", f"Booked for {slot.isoformat()} ({lead_tz.key} business hours)")
    try:
        db.commit()
    except IntegrityError:
        # ux_email_drafts_user_slot fired: something bypassed the advisory lock.
        db.rollback()
        raise ConflictError("That sending slot was just taken — please try again.")
    return _draft_out(draft)


@router.delete("/drafts/{draft_id}/schedule", response_model=EmailDraftOut)
def cancel_schedule(
    draft_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Releases a booked slot (only while still SCHEDULED — a SENDING dispatch is in
    flight and can no longer be stopped)."""
    draft = db.scalars(select(EmailDraft).where(EmailDraft.public_id == draft_id).with_for_update()).first()
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    if draft.status != DraftStatus.SCHEDULED:
        raise ValidationFailedError("This email is not scheduled.")
    _log_dispatch(db, draft, "cancelled", "Schedule cancelled by user.")
    draft.status = DraftStatus.READY
    draft.scheduled_at = None
    draft.scheduled_by_user_id = None
    db.commit()
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/resolve", response_model=EmailDraftOut)
def resolve_needs_attention(
    draft_id: UUID,
    payload: DispatchResolveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailDraftOut:
    """Manual resolution for a dispatch interrupted mid-send (NEEDS_ATTENTION): after
    checking the Sent folder, the user either marks it sent or resets it to READY."""
    draft = db.scalars(select(EmailDraft).where(EmailDraft.public_id == draft_id).with_for_update()).first()
    if not draft:
        raise NotFoundError(f"Draft {draft_id} not found.")
    assert_import_owned(draft.lead.lead_import, user)
    if draft.status != DraftStatus.NEEDS_ATTENTION:
        raise ValidationFailedError("This email does not need attention.")

    if payload.resolution == "mark_sent":
        draft.status = DraftStatus.SENT
        draft.sent_at = draft.sent_at or datetime.now(timezone.utc)
        _log_dispatch(db, draft, "resolved_mark_sent", "User confirmed the email in their Sent folder.")
    else:  # retry — back to READY, user can send/schedule again
        draft.status = DraftStatus.READY
        _log_dispatch(db, draft, "resolved_retry", "User confirmed the email did NOT go out; reset to ready.")
    draft.scheduled_at = None
    draft.scheduled_by_user_id = None
    draft.error_message = None
    db.commit()
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/send", response_model=EmailDraftOut)
def send_draft(
    draft_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> EmailDraftOut:
    """Manual send: ignores business hours, keeps only a 30-second gap from other
    dispatches. Clear of conflicts -> dispatched inline right now. Conflict within
    30s -> booked at the earliest clear instant (<= ~30s away, status SCHEDULED,
    picked up by the dispatcher within its 15s poll)."""
    repo = EmailDraftRepository(db)
    draft = _get_dispatchable_draft(db, draft_id, user)
    mailbox = _connected_mailbox(db, user)

    scheduling.acquire_user_schedule_lock(db, user.id)
    now = scheduling.db_now(db)
    slot = scheduling.allocate_send_slot(db, user.id, now, exclude_draft_id=draft.id)

    if slot > now:
        # Another dispatch is within 30s — defer (max ~30s) instead of clashing.
        draft.status = DraftStatus.SCHEDULED
        draft.scheduled_at = slot
        draft.scheduled_by_user_id = user.id
        draft.attempt_count = 0
        draft.error_message = None
        _log_dispatch(db, draft, "manual_deferred", f"Another dispatch within 30s — sending at {slot.isoformat()}")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ConflictError("That sending slot was just taken — please try again.")
        return _draft_out(draft)

    # Clear to send now. Claim first (SENDING + Message-ID committed BEFORE SMTP),
    # so a crash mid-send is flagged by the sweeper instead of double-dispatched.
    draft.status = DraftStatus.SENDING
    draft.scheduled_at = now  # occupies the slot for the 30s/2min gap checks
    draft.scheduled_by_user_id = user.id
    draft.attempt_count += 1
    draft.message_id = make_msgid(domain=mailbox.email_address.split("@")[-1])
    try:
        db.commit()  # releases the advisory + row locks; the claim is durable
    except IntegrityError:
        db.rollback()
        raise ConflictError("That sending slot was just taken — please try again.")

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
            message_id=draft.message_id,
        )
    except AppException as exc:
        # Release the claim back to READY (not FAILED — that status means AI
        # generation failed) so the user can just retry, with the reason persisted.
        _log_dispatch(db, draft, "failed", str(exc.message))
        repo.update(
            draft,
            status=DraftStatus.READY,
            scheduled_at=None,
            scheduled_by_user_id=None,
            error_message=str(exc.message)[:1000],
        )
        raise

    _log_dispatch(db, draft, "manual_sent")
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

    if draft.channel != DraftChannel.EMAIL:
        raise ValidationFailedError("LinkedIn messages and call scripts are drafts only — they are never dispatched.")
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
