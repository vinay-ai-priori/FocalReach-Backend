from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class Notification(Base, PublicIDMixin, TimestampMixin):
    """In-app nudge shown in the campaign header bell. Kinds: follow_up_due (a lead's
    last outreach email has gone unanswered past its cadence window), and the
    reply-poller kinds raised by app/services/inbox/reply_router.py — reply_negative,
    reply_neutral, reply_booked. Nothing is ever sent or generated automatically from
    a notification itself; it only routes the user to the lead (except reply_negative
    is auto-scheduled and reply_positive auto-sends a follow-up — see reply_router).

    At most one UNREAD notification per (lead, kind) is enforced by the partial unique
    index ux_notifications_lead_kind_unread, so the beat task can insert blindly."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(50), default="follow_up_due", nullable=False)
    # Which sequence step the nudge points at (2/3/4 = follow-up 1/2/3). Unused
    # (null) for reply-poller kinds, which aren't tied to a fixed sequence step.
    due_step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-text summary shown in the bell for kinds without a fixed step label —
    # e.g. a reply excerpt or the extracted booking date/time.
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead")
