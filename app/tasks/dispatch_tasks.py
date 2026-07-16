"""Outreach dispatch engine (Celery beat).

- `outreach.dispatch_due` (every 15s): claims due SCHEDULED drafts with
  FOR UPDATE SKIP LOCKED (multi-worker safe), flips each to SENDING and COMMITS
  BEFORE touching SMTP — so a crash mid-send leaves an explicit SENDING row instead
  of silently double-dispatching. Guarantee: at-most-once delivery.
- `outreach.sweep_stuck` (every 60s): rows stuck in SENDING for > 5 minutes become
  NEEDS_ATTENTION for manual resolution (verify Sent folder via the stamped
  Message-ID, then retry or mark sent).
- Heartbeat in Redis so a dead beat/worker is detectable in minutes.
"""

from datetime import timedelta, timezone
from email.utils import make_msgid
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.crypto import decrypt_secret
from app.core.exceptions import AppException
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.email_draft import DispatchLog, DraftStatus, EmailDraft
from app.repositories.mailbox_repository import MailboxConnectionRepository
from app.services.mailbox.connection_service import send_email_via_smtp
from app.services.mailbox.providers import get_preset
from app.services.scheduling_service import (
    SCHEDULE_GAP,
    acquire_user_schedule_lock,
    allocate_scheduled_slot,
    db_now,
)

configure_logging()
logger = get_logger(__name__)

MAX_DISPATCH_ATTEMPTS = 3
STUCK_SENDING_AFTER = timedelta(minutes=5)
CLAIM_BATCH_SIZE = 20
HEARTBEAT_KEY = "outreach:dispatcher:heartbeat"


def _log(db, draft: EmailDraft, outcome: str, detail: str | None = None) -> None:
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


def _release_to_ready(db, draft: EmailDraft, outcome: str, reason: str) -> None:
    """Give the slot back and surface the reason on the draft (READY = user can act)."""
    _log(db, draft, outcome, reason)
    draft.status = DraftStatus.READY
    draft.scheduled_at = None
    draft.scheduled_by_user_id = None
    draft.error_message = reason[:1000]
    db.commit()


def _heartbeat() -> None:
    try:
        from datetime import datetime

        from app.core.redis_client import get_redis

        get_redis().set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat(), ex=120)
    except Exception:  # heartbeat is observability, never a reason to skip dispatching
        logger.warning("dispatcher heartbeat write failed", exc_info=True)


@celery_app.task(name="outreach.dispatch_due")
def dispatch_due_emails() -> dict:
    _heartbeat()
    db = SessionLocal()
    dispatched, deferred = 0, 0
    try:
        now = db_now(db)
        # Claim phase: SKIP LOCKED means concurrent workers each grab disjoint rows.
        due = list(
            db.scalars(
                select(EmailDraft)
                .where(EmailDraft.status == DraftStatus.SCHEDULED, EmailDraft.scheduled_at <= now)
                .order_by(EmailDraft.scheduled_at)
                .limit(CLAIM_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        )
        for draft in due:
            draft.status = DraftStatus.SENDING
            draft.attempt_count += 1
        db.commit()  # claims are durable BEFORE any SMTP call

        for draft in due:
            ok = _dispatch_one(db, draft)
            dispatched += 1 if ok else 0
            deferred += 0 if ok else 1
        return {"claimed": len(due), "sent": dispatched, "not_sent": deferred}
    finally:
        db.close()


def _dispatch_one(db, draft: EmailDraft) -> bool:
    """Send one claimed (SENDING) draft. Returns True only on a confirmed SMTP send.
    Every exit path leaves the draft in an explicit state + a dispatch_log row."""
    lead = draft.lead

    # Fire-time re-checks: the world may have changed since the slot was claimed.
    if lead.outreach_paused:
        _release_to_ready(db, draft, "skipped_paused", "Skipped: outreach was paused for this lead before dispatch.")
        return False
    if not lead.email:
        _release_to_ready(db, draft, "skipped_no_email", "Skipped: the lead no longer has an email address.")
        return False
    if not draft.subject or not draft.body:
        _release_to_ready(db, draft, "skipped_empty", "Skipped: the draft has no subject/body.")
        return False

    user_id = lead.lead_import.user_id if lead.lead_import else None
    mailbox = None
    if user_id:
        mailboxes = MailboxConnectionRepository(db).list_for_user(user_id)
        mailbox = next((m for m in mailboxes if m.is_connected), None)
    if not mailbox:
        _release_to_ready(db, draft, "skipped_no_mailbox", "Skipped: no connected mailbox at dispatch time.")
        return False

    # Stamp the Message-ID BEFORE sending so an interrupted dispatch is verifiable
    # against the Sent folder.
    if not draft.message_id:
        draft.message_id = make_msgid(domain=mailbox.email_address.split("@")[-1])
        db.commit()

    # Thread this send into the lead's ongoing conversation: In-Reply-To is the most
    # recent prior sent message, References is the full chain — this is both what
    # makes the prospect's mail client group everything as one thread, and what the
    # reply poller matches inbound replies back against.
    prior_message_ids = [
        mid
        for (mid,) in db.execute(
            select(EmailDraft.message_id)
            .where(
                EmailDraft.lead_id == draft.lead_id,
                EmailDraft.status == DraftStatus.SENT,
                EmailDraft.message_id.is_not(None),
                EmailDraft.id != draft.id,
            )
            .order_by(EmailDraft.sent_at)
        )
    ]

    preset = get_preset(mailbox.provider)
    try:
        app_password = decrypt_secret(mailbox.encrypted_app_password)
        send_email_via_smtp(
            preset,
            mailbox.email_address,
            app_password,
            to=lead.email,
            subject=draft.subject,
            body=draft.body,
            message_id=draft.message_id,
            in_reply_to=prior_message_ids[-1] if prior_message_ids else None,
            references=" ".join(prior_message_ids) if prior_message_ids else None,
        )
    except AppException as exc:
        return _handle_send_failure(db, draft, user_id, str(exc.message), getattr(exc, "transient", False))
    except Exception as exc:  # decrypt/preset/unknown errors — never retry blindly
        logger.exception("Unexpected dispatch error for draft %s", draft.id)
        return _handle_send_failure(db, draft, user_id, f"Unexpected dispatch error: {exc}", False)

    now = db_now(db)
    draft.status = DraftStatus.SENT
    draft.sent_at = now
    draft.error_message = None
    _log(db, draft, "sent")
    db.commit()
    return True


def _handle_send_failure(db, draft: EmailDraft, user_id: int | None, reason: str, transient: bool) -> bool:
    if transient and user_id and draft.attempt_count < MAX_DISPATCH_ATTEMPTS:
        # Re-book through the allocator so the retry still honours business hours
        # and never steals a 2-minute neighbourhood from another dispatch.
        try:
            tz = ZoneInfo(draft.lead.timezone) if draft.lead.timezone else timezone.utc
        except Exception:
            tz = timezone.utc
        acquire_user_schedule_lock(db, user_id)
        earliest = db_now(db) + SCHEDULE_GAP
        slot = allocate_scheduled_slot(db, user_id, tz, earliest, exclude_draft_id=draft.id)
        draft.scheduled_at = slot
        draft.status = DraftStatus.SCHEDULED
        draft.error_message = f"{reason} — retrying (attempt {draft.attempt_count}/{MAX_DISPATCH_ATTEMPTS})"[:1000]
        _log(db, draft, "retry_scheduled", reason)
        db.commit()
        return False

    _release_to_ready(db, draft, "failed", reason)
    return False


@celery_app.task(name="outreach.sweep_stuck")
def sweep_stuck_dispatches() -> dict:
    """SENDING rows older than 5 minutes were interrupted mid-send: SMTP outcome is
    UNKNOWN, so they are never auto-retried — flag for manual resolution instead."""
    db = SessionLocal()
    try:
        cutoff = db_now(db) - STUCK_SENDING_AFTER
        stuck = list(
            db.scalars(
                select(EmailDraft)
                .where(EmailDraft.status == DraftStatus.SENDING, EmailDraft.updated_at < cutoff)
                .with_for_update(skip_locked=True)
            )
        )
        for draft in stuck:
            draft.status = DraftStatus.NEEDS_ATTENTION
            draft.error_message = (
                "Dispatch was interrupted mid-send and the outcome is unknown. Check your mailbox's "
                f"Sent folder (Message-ID {draft.message_id or 'not stamped'}) before retrying."
            )[:1000]
            _log(db, draft, "stuck", "SENDING for over 5 minutes — flagged for manual resolution.")
            logger.error("Draft %s stuck in SENDING — flagged NEEDS_ATTENTION", draft.id)
        db.commit()
        return {"flagged": len(stuck)}
    finally:
        db.close()
