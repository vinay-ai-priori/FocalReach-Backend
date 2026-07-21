"""Routes a classified inbound reply to its intent-specific action. Called once per
InboundReply by app/tasks/inbox_poll_tasks.py, right after intent_classifier runs.

Every reply, on arrival, first stops the OLD fixed-cadence follow-up sequence for the
lead (cancelling any already-SCHEDULED follow-up so it can't fire mid-conversation) and
pauses outreach — a reply of any kind means the conversation has moved on from the
automated nudge cadence. From there the intent (two-way, see intent_classifier.py):

- need_reply: a NEEDS_REPLY pending-booking row is created so the reply surfaces on the
  Discovery page's "Need Reply" list, and the bell is nudged. A human replies manually.
- booking_pending: the proposed date/time is extracted and (when resolvable) handed to
  the Cal.com booking orchestrator; the row surfaces under "Booking Pending".
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
    DraftChannel,
    DraftStatus,
    EmailDraft,
)
from app.models.inbound_reply import InboundReply, ReplyIntent
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.pending_booking import PendingBooking, PendingBookingStatus, TimezoneSource
from app.repositories.calcom_repository import CalComConnectionRepository
from app.services.inbox.datetime_extractor import extract_datetime, resolve_to_instant
from app.services.inbox.intent_classifier import classify_reply

logger = get_logger(__name__)


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

    user_id = lead.lead_import.campaign.user_id if lead.lead_import else None
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
        inbound_reply.intent_detection = result.detection
        db.commit()

        _cancel_scheduled_followups(db, lead)

        excerpt = (inbound_reply.body_text or "").strip().replace("\n", " ")[:300]

        # A reply of either kind pauses the automated cadence — the conversation is now
        # a human one. BOOKING_PENDING additionally tries to auto-resolve the date/time.
        _pause_lead(db, lead)
        if result.intent == ReplyIntent.BOOKING_PENDING:
            _handle_booking_pending(db, lead, user_id, inbound_reply)
        else:
            _handle_need_reply(db, lead, user_id, inbound_reply, excerpt)

        inbound_reply.processed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.exception("Failed to route inbound reply %s", inbound_reply.id)
        db.rollback()
        inbound_reply.processing_error = str(exc)[:1000]
        inbound_reply.processed_at = datetime.now(timezone.utc)
        db.commit()


def _handle_need_reply(db: Session, lead: Lead, user_id: int, inbound_reply: InboundReply, excerpt: str) -> None:
    """A reply with no schedulable date/time. Surface it on the Discovery page's
    "Need Reply" list (a NEEDS_REPLY pending-booking row) and nudge the bell."""
    db.add(
        PendingBooking(
            lead_id=lead.id,
            inbound_reply_id=inbound_reply.id,
            user_id=user_id,
            status=PendingBookingStatus.NEEDS_REPLY,
            timezone_source=TimezoneSource.UNKNOWN,
        )
    )
    db.commit()
    _notify(db, user_id, lead, "reply_need_reply", f"Replied — needs your response: “{excerpt}”")


def _handle_booking_pending(db: Session, lead: Lead, user_id: int, inbound_reply: InboundReply) -> None:
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

    _notify(db, user_id, lead, "reply_booking_pending", detail)


def enqueue_booking_processing(booking_id: int) -> None:
    """Seam for tests; imports lazily to keep celery out of this module's import path."""
    from app.tasks.booking_tasks import process_pending

    process_pending.delay(booking_id)
