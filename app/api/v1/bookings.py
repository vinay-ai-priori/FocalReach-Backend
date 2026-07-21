"""Review + resolution surface for pending bookings (the Discovery Calls dashboard).

Closes the NEEDS_REVIEW loop: every booking the automation couldn't finish is listed
here with its reason, and the user resolves it by either booking a slot manually
(calendar -> day slots -> confirm), sending the lead an availability email, or
dismissing it. BOOKED/AWAITING_RESLOT rows are shown for real-time tracking."""

from datetime import timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.models.pending_booking import PendingBooking, PendingBookingStatus
from app.models.user import User
from app.repositories.calcom_repository import CalComConnectionRepository
from app.schemas.bookings import BookingOut, ManualBookRequest
from app.services.calcom import booking_orchestrator
from app.services.calcom.client import calcom_client
from app.services.calcom.slots import parse_slot_start
from app.services.calcom.token_service import get_valid_access_token

logger = get_logger(__name__)

router = APIRouter(prefix="/bookings", tags=["bookings"], dependencies=[Depends(get_current_user)])

# Statuses whose booking actions (book manually / suggest another time) still apply.
BOOKABLE = (PendingBookingStatus.PENDING, PendingBookingStatus.NEEDS_REVIEW, PendingBookingStatus.AWAITING_RESLOT)
# Rows a user can dismiss/decline — the bookable ones plus "need reply" cards.
DISMISSABLE = (*BOOKABLE, PendingBookingStatus.NEEDS_REPLY)


def _booking_out(b: PendingBooking) -> BookingOut:
    lead = b.lead
    reply = b.inbound_reply
    excerpt = (reply.body_text or "").strip().replace("\n", " ")[:300] if reply else None
    category = "need_reply" if b.status == PendingBookingStatus.NEEDS_REPLY else "booking_pending"
    return BookingOut(
        public_id=b.public_id,
        status=b.status.value,
        category=category,
        detection=reply.intent_detection if reply else None,
        resolved_start=b.resolved_start,
        resolved_timezone=b.resolved_timezone,
        timezone_source=b.timezone_source.value if b.timezone_source else None,
        last_error=b.last_error,
        calcom_booking_uid=b.calcom_booking_uid,
        meeting_url=b.meeting_url,
        created_at=b.created_at,
        updated_at=b.updated_at,
        lead_public_id=lead.public_id if lead else None,
        lead_name=lead.full_name if lead else None,
        lead_email=lead.email if lead else None,
        company_name=lead.company.name if lead and lead.company else None,
        reply_subject=reply.subject if reply else None,
        reply_excerpt=excerpt or None,
    )


def _get_owned_booking(db: Session, booking_id: UUID, user: User, *, for_update: bool = False) -> PendingBooking:
    stmt = select(PendingBooking).where(
        PendingBooking.public_id == booking_id, PendingBooking.user_id == user.id
    )
    if for_update:
        stmt = stmt.with_for_update()
    booking = db.scalars(stmt).first()
    if not booking:
        raise NotFoundError(f"Booking {booking_id} not found.")
    return booking


@router.get("", response_model=list[BookingOut])
def list_bookings(
    status: str | None = Query(default=None, description="Filter by status value, e.g. needs_review"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BookingOut]:
    stmt = (
        select(PendingBooking)
        .where(PendingBooking.user_id == user.id)
        .order_by(PendingBooking.created_at.desc())
        .limit(200)
    )
    if status:
        try:
            stmt = stmt.where(PendingBooking.status == PendingBookingStatus(status))
        except ValueError:
            raise ValidationFailedError(f"'{status}' is not a valid booking status.")
    return [_booking_out(b) for b in db.scalars(stmt)]


@router.post("/{booking_id}/book", response_model=BookingOut)
def book_manually(
    booking_id: UUID,
    payload: ManualBookRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BookingOut:
    """Books the user-picked slot (from GET /calcom/slots/day) for this booking's lead.
    Same at-most-once claim pattern as the orchestrator: PENDING->BOOKING is committed
    before the Cal.com call; a stale slot comes back as a 409 with the row restored."""
    booking = _get_owned_booking(db, booking_id, user, for_update=True)
    if booking.status not in BOOKABLE:
        raise ValidationFailedError(f"This booking is already {booking.status.value} — nothing to book.")
    if not booking.lead or not booking.lead.email:
        raise ValidationFailedError("The lead has no email address to book the meeting for.")

    start = parse_slot_start(payload.start)
    if start is None:
        raise ValidationFailedError(f"'{payload.start}' is not a valid slot start time.")

    connection = CalComConnectionRepository(db).get_for_user(user.id)
    if not connection or not connection.is_connected:
        raise ValidationFailedError("Connect your Cal.com account before booking.")
    if not connection.selected_event_type_id:
        raise ValidationFailedError("Select a Cal.com event type before booking.")

    access_token = get_valid_access_token(db, user.id)

    previous_status = booking.status
    booking.status = PendingBookingStatus.BOOKING
    db.commit()

    try:
        result = calcom_client.create_booking(
            access_token,
            event_type_id=connection.selected_event_type_id,
            start=start.astimezone(timezone.utc).isoformat(),
            timezone_name=connection.timezone,
            attendee_name=booking.lead.full_name or booking.lead.email,
            attendee_email=booking.lead.email,
        )
    except Exception as exc:
        booking.status = previous_status
        booking.last_error = str(exc)[:1024]
        db.commit()
        raise ConflictError("Cal.com couldn't book that slot (it may have just been taken) — pick another one.")

    booking.status = PendingBookingStatus.BOOKED
    booking.resolved_start = start.astimezone(timezone.utc)
    booking.resolved_timezone = connection.timezone
    booking.calcom_booking_uid = str(result.get("uid") or result.get("id") or "")[:255] or None
    booking.meeting_url = result.get("meetingUrl") or result.get("location") or None
    booking.last_error = None
    db.commit()
    when = booking_orchestrator.format_slot_display(booking.resolved_start, connection.timezone)
    booking_orchestrator._notify(db, user.id, booking.lead_id, "booking_confirmed", f"Meeting booked: {when} ✅")
    return _booking_out(booking)


@router.post("/{booking_id}/send-alternatives", response_model=BookingOut)
def send_alternatives(
    booking_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BookingOut:
    """Emails the lead the next available slots ("that time isn't available / here's
    my availability") via the drafter agent + the collision-safe dispatch pathway."""
    booking = _get_owned_booking(db, booking_id, user, for_update=True)
    if booking.status not in BOOKABLE:
        raise ValidationFailedError(f"This booking is already {booking.status.value}.")
    booking_orchestrator.send_alternatives_for_booking(db, booking)
    return _booking_out(booking)


@router.post("/{booking_id}/dismiss", response_model=BookingOut)
def dismiss(
    booking_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BookingOut:
    booking = _get_owned_booking(db, booking_id, user, for_update=True)
    if booking.status == PendingBookingStatus.BOOKED:
        raise ValidationFailedError("This meeting is already booked on Cal.com — cancel it there instead.")
    if booking.status not in DISMISSABLE:
        raise ValidationFailedError(f"This booking is already {booking.status.value}.")
    booking.status = PendingBookingStatus.CANCELLED
    db.commit()
    return _booking_out(booking)


@router.post("/{booking_id}/mark-unread", response_model=BookingOut)
def mark_unread(
    booking_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BookingOut:
    """Re-surface a Discovery card in the bell: raises a fresh unread notification for
    this row's lead. Idempotent — the partial unique index on (lead, kind, unread)
    means a duplicate insert while one is already unread is silently a no-op."""
    booking = _get_owned_booking(db, booking_id, user)
    kind = "reply_need_reply" if booking.status == PendingBookingStatus.NEEDS_REPLY else "reply_booking_pending"
    reply = booking.inbound_reply
    excerpt = (reply.body_text or "").strip().replace("\n", " ")[:300] if reply else ""
    detail = (
        f"Replied — needs your response: “{excerpt}”"
        if kind == "reply_need_reply"
        else f"Wants to book a call: “{excerpt}”"
    )
    booking_orchestrator._notify(db, user.id, booking.lead_id, kind, detail)
    return _booking_out(booking)
