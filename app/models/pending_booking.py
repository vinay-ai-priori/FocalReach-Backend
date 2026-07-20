import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class PendingBookingStatus(str, enum.Enum):
    PENDING = "pending"  # date/time resolved — queued for the booking orchestrator
    NEEDS_REVIEW = "needs_review"  # extraction failed/ambiguous OR automation hit an error — human decides
    BOOKING = "booking"  # transient claim while the Cal.com booking API call is in flight
    AWAITING_RESLOT = "awaiting_reslot"  # requested slot unavailable — alternatives emailed to the lead
    BOOKED = "booked"
    CANCELLED = "cancelled"


class TimezoneSource(str, enum.Enum):
    EXPLICIT = "explicit"  # the reply stated a timezone
    LEAD_COUNTRY = "lead_country"  # derived from the lead's country
    UNKNOWN = "unknown"  # neither available — resolved_start is left null


class PendingBooking(Base, PublicIDMixin, TimestampMixin):
    """A BOOKED-intent reply's extracted meeting time. PENDING rows are picked up by
    the booking orchestrator (app/services/calcom/booking_orchestrator.py), which
    either books the slot on Cal.com, or emails the lead alternative slots when the
    requested time is outside working hours / already taken."""

    __tablename__ = "pending_bookings"
    __table_args__ = (
        # One live booking attempt per inbound reply — cancelled ones don't block a retry.
        Index(
            "ux_pending_bookings_reply_live",
            "inbound_reply_id",
            unique=True,
            postgresql_where=text("status != 'CANCELLED'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    inbound_reply_id: Mapped[int] = mapped_column(
        ForeignKey("inbound_replies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    status: Mapped[PendingBookingStatus] = mapped_column(
        Enum(PendingBookingStatus, name="pending_booking_status"),
        default=PendingBookingStatus.NEEDS_REVIEW,
        nullable=False,
    )
    # The instant the prospect asked for, converted to the rep's Cal.com timezone (or
    # left null if extraction failed / was too ambiguous to resolve — see status).
    resolved_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timezone_source: Mapped[TimezoneSource | None] = mapped_column(
        Enum(TimezoneSource, name="timezone_source"), nullable=True
    )
    # Raw LLM extraction output (date/time/timezone/confidence as given) — kept for
    # debugging bad extractions and for manual resolution when status=needs_review.
    raw_extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Outcome of the automated Cal.com booking (set by the orchestrator).
    calcom_booking_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    lead = relationship("Lead")
    inbound_reply = relationship("InboundReply")
