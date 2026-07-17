"""Celery entry points for the Cal.com booking orchestrator.

- `booking.process_pending` — processes one PENDING pending_booking (enqueued by
  reply_router the moment a BOOKED reply resolves to a concrete instant).
- `booking.sweep_stale` (beat, every 5 min) — belt-and-braces: picks up PENDING rows
  older than a minute whose enqueue was lost (broker hiccup, worker restart), and
  flags BOOKING rows stuck mid-claim for > 5 minutes as NEEDS_REVIEW (the Cal.com
  call outcome is unknown — a human must check the calendar before retrying).
"""

from datetime import timedelta

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.pending_booking import PendingBooking, PendingBookingStatus
from app.services.calcom.booking_orchestrator import process_pending_booking
from app.services.scheduling_service import db_now

configure_logging()
logger = get_logger(__name__)

STALE_PENDING_AFTER = timedelta(minutes=1)
STUCK_BOOKING_AFTER = timedelta(minutes=5)


@celery_app.task(name="booking.process_pending")
def process_pending(booking_id: int) -> str:
    db = SessionLocal()
    try:
        outcome = process_pending_booking(db, booking_id)
        logger.info("booking.process_pending %s -> %s", booking_id, outcome)
        return outcome
    finally:
        db.close()


@celery_app.task(name="booking.sweep_stale")
def sweep_stale() -> dict:
    db = SessionLocal()
    processed = flagged = 0
    try:
        now = db_now(db)

        stale_pending = list(
            db.scalars(
                select(PendingBooking.id).where(
                    PendingBooking.status == PendingBookingStatus.PENDING,
                    PendingBooking.created_at < now - STALE_PENDING_AFTER,
                )
            )
        )
        for booking_id in stale_pending:
            try:
                process_pending_booking(db, booking_id)
                processed += 1
            except Exception:
                logger.exception("booking sweep failed for pending_booking %s", booking_id)
                db.rollback()

        stuck = list(
            db.scalars(
                select(PendingBooking)
                .where(
                    PendingBooking.status == PendingBookingStatus.BOOKING,
                    PendingBooking.updated_at < now - STUCK_BOOKING_AFTER,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for booking in stuck:
            booking.status = PendingBookingStatus.NEEDS_REVIEW
            booking.last_error = (
                "Booking was interrupted mid-call and the outcome is unknown — check the Cal.com "
                "calendar before retrying."
            )
            flagged += 1
            logger.error("pending_booking %s stuck in BOOKING — flagged NEEDS_REVIEW", booking.id)
        if stuck:
            db.commit()

        return {"stale_processed": processed, "stuck_flagged": flagged}
    finally:
        db.close()
