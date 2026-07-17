"""Automated Cal.com booking for PENDING pending_bookings.

Flow (per booking, invoked via app/tasks/booking_tasks.py):
1. Guards: booking still PENDING, user has a connected Cal.com account with a
   selected event type — otherwise NEEDS_REVIEW + notification (the old manual path).
2. Cheap pre-check against the working days/hours cached in our DB (in the USER's
   Cal.com timezone). Outside them -> alternatives path with no wasted slots call
   for the requested day.
3. Authoritative check: Cal.com's /slots for the requested day (it already accounts
   for BOTH the schedule and existing bookings). Requested instant present ->
   claim (status=BOOKING, committed) -> create the booking -> BOOKED + notify.
4. Unavailable (out-of-hours OR slot taken OR in the past) -> fetch the next 5
   available slots, have the drafter agent write a "that time isn't available"
   reply listing them in the LEAD's timezone, dispatch it through the collision-safe
   scheduling pathway -> AWAITING_RESLOT + notify.
5. Any external failure -> NEEDS_REVIEW with last_error + notification. Never silent.

At-most-once booking guarantee: the PENDING->BOOKING flip is committed BEFORE the
booking API call (same claim pattern as the email dispatcher); a crash mid-call leaves
an explicit BOOKING row that the sweeper flags for manual review instead of re-firing.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.models.email_draft import STEP_SLOT_ALTERNATIVES, DraftChannel, DraftStatus, EmailDraft
from app.models.notification import Notification
from app.models.pending_booking import PendingBooking, PendingBookingStatus
from app.repositories.calcom_repository import CalComConnectionRepository
from app.services.calcom.client import calcom_client
from app.services.calcom.slots import filter_future_slots, parse_slot_start
from app.services.calcom.token_service import get_valid_access_token
from app.services.inbox.alternative_slots_drafter import draft_alternative_slots_email
from app.services.scheduling_service import acquire_user_schedule_lock, allocate_send_slot, db_now

logger = get_logger(__name__)

ALTERNATIVE_SLOTS_COUNT = 5
ALTERNATIVE_SLOTS_HORIZON_DAYS = 14


# ------------------------------------------------------------ pure helpers ---

def is_within_working_hours(
    instant_utc: datetime, tz_name: str, working_days: list[str], start_hhmm: str, end_hhmm: str
) -> bool:
    """True when `instant_utc` falls on one of `working_days` between start and end
    (start inclusive, end exclusive), evaluated in the user's Cal.com timezone.
    Unparseable inputs return True — the authoritative Cal.com slots check follows
    anyway, and a broken cached value must not wrongly reject a bookable time."""
    try:
        local = instant_utc.astimezone(ZoneInfo(tz_name))
        return local.strftime("%A") in working_days and start_hhmm <= local.strftime("%H:%M") < end_hhmm
    except Exception:
        return True


def find_matching_slot(raw_slots: list[dict], requested_utc: datetime) -> dict | None:
    """The Cal.com slot whose start is exactly the requested instant, if any."""
    for slot in raw_slots:
        start = parse_slot_start(slot.get("start"))
        if start is not None and start == requested_utc:
            return slot
    return None


def format_slot_display(instant_utc: datetime, tz_name: str) -> str:
    """'Monday, Jul 20 at 2:30 PM IST' in the given timezone."""
    local = instant_utc.astimezone(ZoneInfo(tz_name))
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.strftime('%A, %b %d')} at {hour}:{local.strftime('%M %p %Z')}"


def _lead_display_timezone(lead, user_tz: str) -> str:
    """Timezone the alternatives are rendered in: the lead's own when known,
    otherwise the user's — never raw UTC unless nothing else exists."""
    if lead.timezone:
        try:
            ZoneInfo(lead.timezone)
            return lead.timezone
        except Exception:
            pass
    return user_tz


# ------------------------------------------------------------ side effects ---

def _notify(db: Session, user_id: int, lead_id: int, kind: str, detail: str) -> None:
    from sqlalchemy.exc import IntegrityError

    try:
        db.add(Notification(user_id=user_id, lead_id=lead_id, kind=kind, detail=detail[:500]))
        db.commit()
    except IntegrityError:
        db.rollback()  # an unread notification of this kind for this lead already exists


def _mark_needs_review(db: Session, booking: PendingBooking, reason: str) -> str:
    booking.status = PendingBookingStatus.NEEDS_REVIEW
    booking.last_error = reason[:1024]
    db.commit()
    _notify(db, booking.user_id, booking.lead_id, "booking_needs_review", f"Couldn't book automatically: {reason}")
    return "needs_review"


def _dispatch_alternatives_email(
    db: Session, booking: PendingBooking, subject: str, body: str
) -> None:
    """Same collision-safe pathway as every other auto-sent reply: READY draft ->
    advisory lock -> allocate_send_slot -> SCHEDULED (dispatcher picks it up)."""
    draft = EmailDraft(
        lead_id=booking.lead_id,
        channel=DraftChannel.EMAIL,
        step_index=STEP_SLOT_ALTERNATIVES,
        status=DraftStatus.READY,
        subject=subject,
        body=body,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    acquire_user_schedule_lock(db, booking.user_id)
    slot = allocate_send_slot(db, booking.user_id, db_now(db), exclude_draft_id=draft.id)
    draft.status = DraftStatus.SCHEDULED
    draft.scheduled_at = slot
    draft.scheduled_by_user_id = booking.user_id
    db.commit()


# -------------------------------------------------------------- main entry ---

def process_pending_booking(db: Session, booking_id: int) -> str:
    """Returns an outcome tag: booked | alternatives_sent | needs_review | skipped."""
    booking = db.scalars(
        select(PendingBooking).where(PendingBooking.id == booking_id).with_for_update(skip_locked=True)
    ).first()
    if not booking or booking.status != PendingBookingStatus.PENDING:
        return "skipped"  # already handled (or being handled) elsewhere — idempotent
    if booking.resolved_start is None:
        return _mark_needs_review(db, booking, "No resolved date/time on this booking.")

    connection = CalComConnectionRepository(db).get_for_user(booking.user_id)
    if not connection or not connection.is_connected:
        return _mark_needs_review(db, booking, "Cal.com is not connected.")
    if not connection.selected_event_type_id:
        return _mark_needs_review(db, booking, "No Cal.com event type selected.")

    requested = booking.resolved_start
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)
    now = db_now(db)
    user_tz = connection.timezone

    try:
        access_token = get_valid_access_token(db, booking.user_id)

        available = requested > now and is_within_working_hours(
            requested, user_tz, connection.working_days, connection.working_hours_start, connection.working_hours_end
        )
        if available:
            # Authoritative check — Cal.com's slots already exclude occupied times.
            day_slots = calcom_client.get_slots(
                access_token,
                event_type_id=connection.selected_event_type_id,
                timezone_name=user_tz,
                start=requested,
                end=requested + timedelta(days=1),
            )
            available = find_matching_slot(day_slots, requested) is not None

        if available:
            return _book(db, booking, connection, access_token, requested, user_tz)
        return _send_alternatives(db, booking, connection, access_token, requested, now, user_tz)

    except ExternalServiceError as exc:
        db.rollback()
        booking = db.get(PendingBooking, booking_id)
        if booking and booking.status in (PendingBookingStatus.PENDING, PendingBookingStatus.BOOKING):
            return _mark_needs_review(db, booking, exc.message)
        return "needs_review"


def _book(
    db: Session, booking: PendingBooking, connection, access_token: str, requested: datetime, user_tz: str
) -> str:
    lead = booking.lead
    if not lead.email:
        return _mark_needs_review(db, booking, "The lead has no email address to book the meeting for.")
    # Claim BEFORE the API call: a crash mid-call leaves an explicit BOOKING row for
    # the sweeper, never a double-booked meeting.
    booking.status = PendingBookingStatus.BOOKING
    db.commit()

    result = calcom_client.create_booking(
        access_token,
        event_type_id=connection.selected_event_type_id,
        start=requested.astimezone(timezone.utc).isoformat(),
        timezone_name=booking.resolved_timezone or user_tz,
        attendee_name=lead.full_name or lead.email or "Prospect",
        attendee_email=lead.email,
    )

    booking.status = PendingBookingStatus.BOOKED
    booking.calcom_booking_uid = str(result.get("uid") or result.get("id") or "")[:255] or None
    booking.meeting_url = (result.get("meetingUrl") or result.get("location") or None)
    booking.last_error = None
    db.commit()
    when = format_slot_display(requested, user_tz)
    _notify(db, booking.user_id, booking.lead_id, "booking_confirmed", f"Meeting booked: {when} ✅")
    logger.info("Booked Cal.com meeting for pending_booking %s (uid=%s)", booking.id, booking.calcom_booking_uid)
    return "booked"


def send_alternatives_for_booking(db: Session, booking: PendingBooking) -> str:
    """Public entry for the review UI's "send availability email" action. Works even
    when the booking has no resolved time (ambiguous reply). Returns the outcome tag."""
    connection = CalComConnectionRepository(db).get_for_user(booking.user_id)
    if not connection or not connection.is_connected:
        return _mark_needs_review(db, booking, "Cal.com is not connected.")
    if not connection.selected_event_type_id:
        return _mark_needs_review(db, booking, "No Cal.com event type selected.")
    try:
        access_token = get_valid_access_token(db, booking.user_id)
        return _send_alternatives(
            db, booking, connection, access_token, booking.resolved_start, db_now(db), connection.timezone
        )
    except ExternalServiceError as exc:
        db.rollback()
        return _mark_needs_review(db, booking, exc.message)


def _send_alternatives(
    db: Session, booking: PendingBooking, connection, access_token: str,
    requested: datetime | None, now: datetime, user_tz: str,
) -> str:
    lead = booking.lead
    raw = calcom_client.get_slots(
        access_token,
        event_type_id=connection.selected_event_type_id,
        timezone_name=user_tz,
        start=now,
        end=now + timedelta(days=ALTERNATIVE_SLOTS_HORIZON_DAYS),
    )
    upcoming = filter_future_slots(raw, now)[:ALTERNATIVE_SLOTS_COUNT]
    if not upcoming:
        return _mark_needs_review(
            db, booking, f"Requested slot unavailable and no open slots in the next {ALTERNATIVE_SLOTS_HORIZON_DAYS} days."
        )
    if not lead.email:
        return _mark_needs_review(db, booking, "Requested slot unavailable and the lead has no email address.")

    display_tz = _lead_display_timezone(lead, user_tz)
    slot_displays = [format_slot_display(parse_slot_start(s["start"]), display_tz) for s in upcoming]
    requested_display = format_slot_display(requested, display_tz) if requested else "the time you suggested"

    reply_subject = booking.inbound_reply.subject if booking.inbound_reply else None
    if reply_subject and not reply_subject.lower().startswith("re:"):
        reply_subject = f"Re: {reply_subject}"

    draft = draft_alternative_slots_email(
        lead_name=lead.first_name or lead.full_name,
        requested_time_display=requested_display,
        slot_displays=slot_displays,
        reply_subject=reply_subject,
    )
    _dispatch_alternatives_email(db, booking, reply_subject or draft.subject, draft.body)

    booking.status = PendingBookingStatus.AWAITING_RESLOT
    booking.last_error = None
    db.commit()
    detail = (
        f"Requested time ({requested_display}) wasn't available — emailed {len(slot_displays)} alternative slots."
        if requested
        else f"Emailed {len(slot_displays)} available slots so the lead can pick a time."
    )
    _notify(db, booking.user_id, booking.lead_id, "booking_alternatives", detail)
    return "alternatives_sent"
