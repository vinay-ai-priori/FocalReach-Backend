import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


REFINE_LIMIT = 2  # max Regenerate/Shorter/More Technical/.../Personalize Further calls per draft


class DraftStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    SENT = "sent"
    APPROVED = "approved"


class EmailDraft(Base, PublicIDMixin, TimestampMixin):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)

    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, name="draft_status"), default=DraftStatus.PENDING, nullable=False
    )
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    personalization_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    booking_link: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_cached: Mapped[bool] = mapped_column(default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Superseded versions ({subject, body, refined_with}), oldest first — the "previous
    # drafts in this thread" context for regenerate/refine.
    history: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Last "send test" target + timestamp, used both to remember the address the user
    # tested with and as a cooldown guard against duplicate double-click sends.
    last_test_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    last_test_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Counts every refine/regenerate call against this draft (Regenerate, Shorter, More
    # Technical, More Executive, More Friendly, Personalize Further all share this one
    # pool) — capped at REFINE_LIMIT per draft. Each email step (initial, follow-ups,
    # LinkedIn, call) will get its own row and therefore its own independent counter
    # once those steps become real drafts; today only the initial email exists.
    refine_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # When the REAL send (not a test send) went out — set once, when status flips to
    # SENT. Displayed in place of the action buttons, which disappear once sent.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead", back_populates="email_drafts")
