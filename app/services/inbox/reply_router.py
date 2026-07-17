"""Routes a classified inbound reply to its intent-specific action. Called once per
InboundReply by app/tasks/inbox_poll_tasks.py, right after intent_classifier runs.

Every intent, on arrival, first stops the OLD fixed-cadence follow-up sequence for the
lead (cancelling any already-SCHEDULED follow-up so it can't fire mid-conversation) —
a reply of any kind means the conversation has moved on from the automated nudge
cadence. From there:

- negative/neutral/booked: the lead's outreach is paused outright (nothing further is
  automated — booked still needs a human/Cal.com to actually confirm the meeting).
- positive: outreach is NOT paused — instead a "share your availability" email is
  drafted and dispatched immediately through the existing scheduling-service pathway
  (allocate_send_slot), so it can't collide with any other dispatch for this user.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.email_draft import (
    STEP_FOLLOW_UP_FIRST,
    STEP_FOLLOW_UP_LAST,
    STEP_SCHEDULING_REPLY,
    DraftChannel,
    DraftStatus,
    EmailDraft,
)
from app.models.inbound_reply import InboundReply, ReplyIntent
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.pending_booking import PendingBooking, PendingBookingStatus, TimezoneSource
from app.repositories.calcom_repository import CalComConnectionRepository
from app.repositories.mailbox_repository import MailboxConnectionRepository
from app.services.inbox.datetime_extractor import extract_datetime, resolve_to_instant
from app.services.inbox.intent_classifier import classify_reply
from app.services.scheduling_service import acquire_user_schedule_lock, allocate_send_slot, db_now

logger = get_logger(__name__)

SCHEDULING_REPLY_SUBJECT_FALLBACK = "Let's find a time"
SCHEDULING_REPLY_BODY = (
    "Great — could you share a date and time that works for you (and your timezone)? "
    "I'll get something on the calendar right away."
)


def _cancel_scheduled_followups(db: Session, lead: Lead) -> None:
    """Any already-SCHEDULED step in the old fixed cadence is pulled back before it
    can auto-fire — a reply means the conversation isn't following that script
    anymore. Steps not yet scheduled need no action; they simply won't be acted on."""
    scheduled = db.scalars(
        select(EmailDraft).where(
            EmailDraft.lead_id == lead.id,
            EmailDraft.channel == DraftChannel.EMAIL,
            EmailDraft.status == DraftStatus.SCHEDULED,
            EmailDraft.step_index >= STEP_FOLLOW_UP_FIRST,
            EmailDraft.step_index <= STEP_FOLLOW_UP_LAST,
        )
    )
    for draft in scheduled:
        draft.status = DraftStatus.READY
        draft.scheduled_at = None
        draft.scheduled_by_user_id = None
        draft.error_message = "Cancelled: the lead replied before this follow-up was due."
    db.commit()


def _pause_lead(db: Session, lead: Lead) -> None:
    lead.outreach_paused = True
    db.commit()


def _notify(db: Session, user_id: int, lead: Lead, kind: str, detail: str, due_step_index: int | None = None) -> None:
    try:
        db.add(Notification(user_id=user_id, lead_id=lead.id, kind=kind, due_step_index=due_step_index, detail=detail[:500]))
        db.commit()
    except IntegrityError:
        db.rollback()  # an unread notification of this kind for this lead already exists


def _dispatch_scheduling_reply(db: Session, lead: Lead, user_id: int, in_reply_to_subject: str | None) -> None:
    subject = f"Re: {in_reply_to_subject}" if in_reply_to_subject else SCHEDULING_REPLY_SUBJECT_FALLBACK
    draft = EmailDraft(
        lead_id=lead.id,
        channel=DraftChannel.EMAIL,
        step_index=STEP_SCHEDULING_REPLY,
        status=DraftStatus.READY,
        subject=subject,
        body=SCHEDULING_REPLY_BODY,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    # Same collision-safe "send now" pathway manual Send uses — never steals another
    # dispatch's slot, and if something's already claiming `now` this just lands a
    # few seconds later instead of colliding.
    acquire_user_schedule_lock(db, user_id)
    slot = allocate_send_slot(db, user_id, db_now(db), exclude_draft_id=draft.id)
    draft.status = DraftStatus.SCHEDULED
    draft.scheduled_at = slot
    draft.scheduled_by_user_id = user_id
    db.commit()


def _lead_fallback_timezone(lead: Lead) -> str | None:
    if lead.timezone:
        return lead.timezone
    if lead.country:
        from app.services.lead_timezone_service import resolve_timezone_for_country

        try:
            result = resolve_timezone_for_country(lead.country)
            return result.timezone
        except Exception:
            return None
    return None


def route_reply(db: Session, inbound_reply: InboundReply) -> None:
    lead = inbound_reply.lead
    if lead is None:
        inbound_reply.processed_at = datetime.now(timezone.utc)
        inbound_reply.processing_error = "No matching lead — reply left unrouted."
        db.commit()
        return

    user_id = lead.lead_import.user_id if lead.lead_import else None
    if user_id is None:
        inbound_reply.processed_at = datetime.now(timezone.utc)
        inbound_reply.processing_error = "Lead has no owning user — reply left unrouted."
        db.commit()
        return

    try:
        result = classify_reply(inbound_reply.subject, inbound_reply.body_text or "")
        inbound_reply.intent = result.intent
        inbound_reply.intent_confidence = result.confidence
        inbound_reply.intent_reason = result.reason
        db.commit()

        _cancel_scheduled_followups(db, lead)

        excerpt = (inbound_reply.body_text or "").strip().replace("\n", " ")[:300]

        if result.intent == ReplyIntent.NEGATIVE:
            _pause_lead(db, lead)
            _notify(db, user_id, lead, "reply_negative", f"Not interested: “{excerpt}”")

        elif result.intent == ReplyIntent.NEUTRAL:
            _pause_lead(db, lead)
            _notify(
                db, user_id, lead, "reply_neutral",
                f"Wants to wait: “{excerpt}” — outreach paused. You can reply manually now, "
                "or resume outreach from the lead's page when the timing is right.",
            )

        elif result.intent == ReplyIntent.BOOKED:
            _pause_lead(db, lead)
            _handle_booked(db, lead, user_id, inbound_reply)

        elif result.intent == ReplyIntent.POSITIVE:
            _dispatch_scheduling_reply(db, lead, user_id, inbound_reply.subject)
            _notify(db, user_id, lead, "reply_positive", f"Interested — asked for their availability: “{excerpt}”")

        inbound_reply.processed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.exception("Failed to route inbound reply %s", inbound_reply.id)
        db.rollback()
        inbound_reply.processing_error = str(exc)[:1000]
        inbound_reply.processed_at = datetime.now(timezone.utc)
        db.commit()


def _handle_booked(db: Session, lead: Lead, user_id: int, inbound_reply: InboundReply) -> None:
    received_at = inbound_reply.received_at or datetime.now(timezone.utc)
    extracted = extract_datetime(inbound_reply.body_text or "", received_at)
    fallback_tz = _lead_fallback_timezone(lead)
    instant_utc, source_tz, source = resolve_to_instant(extracted, fallback_tz)

    calcom = CalComConnectionRepository(db).get_for_user(user_id)
    display_tz = calcom.timezone if (calcom and calcom.is_connected) else source_tz

    resolved_start = None
    if instant_utc is not None:
        resolved_start = instant_utc
        status = PendingBookingStatus.PENDING
    else:
        status = PendingBookingStatus.NEEDS_REVIEW

    booking = PendingBooking(
        lead_id=lead.id,
        inbound_reply_id=inbound_reply.id,
        user_id=user_id,
        status=status,
        resolved_start=resolved_start,
        resolved_timezone=display_tz if resolved_start else None,
        timezone_source=TimezoneSource(source) if resolved_start else TimezoneSource.UNKNOWN,
        raw_extraction=extracted.raw,
    )
    db.add(booking)
    db.commit()

    if resolved_start:
        when = resolved_start.astimezone(
            ZoneInfo(display_tz) if display_tz else timezone.utc
        ).strftime("%a %b %d, %I:%M %p %Z")
        detail = f"Wants to book: {when}. Booking it on Cal.com automatically…"
        # Hand off to the booking orchestrator. A lost enqueue is not fatal — the
        # booking.sweep_stale beat task re-processes PENDING rows within 5 minutes.
        try:
            enqueue_booking_processing(booking.id)
        except Exception:
            logger.warning("Could not enqueue booking processing for pending_booking %s", booking.id, exc_info=True)
    else:
        detail = "Wants to book a call but the date/time wasn't clear enough to auto-resolve — check the reply."

    _notify(db, user_id, lead, "reply_booked", detail)


def enqueue_booking_processing(booking_id: int) -> None:
    """Seam for tests; imports lazily to keep celery out of this module's import path."""
    from app.tasks.booking_tasks import process_pending

    process_pending.delay(booking_id)
