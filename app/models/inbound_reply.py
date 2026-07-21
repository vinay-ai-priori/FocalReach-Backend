import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class ReplyIntent(str, enum.Enum):
    # Two-way taxonomy: a reply either proposes a concrete date/day/time to meet
    # (BOOKING_PENDING) or it doesn't and a human needs to respond (NEED_REPLY).
    # See app/services/inbox/intent_classifier.py.
    NEED_REPLY = "need_reply"
    BOOKING_PENDING = "booking_pending"


class InboundReply(Base, PublicIDMixin, TimestampMixin):
    """One row per inbound message seen by the reply poller (app/services/inbox/
    imap_poll_service.py), whether or not it could be matched to a lead — unmatched
    rows are kept for auditability but never classified/routed. The unique constraint
    on (mailbox_connection_id, imap_message_id) is what makes re-polling the same
    mailbox idempotent: a message already seen is skipped outright."""

    __tablename__ = "inbound_replies"
    __table_args__ = (
        UniqueConstraint("mailbox_connection_id", "imap_message_id", name="uq_inbound_reply_mailbox_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox_connection_id: Mapped[int] = mapped_column(
        ForeignKey("mailbox_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=True, index=True)
    # The most recent EmailDraft sent for that lead's thread at the time this reply
    # arrived — establishes which thread it belongs to for the auto-sent follow-up.
    matched_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_drafts.id", ondelete="SET NULL"), nullable=True
    )

    imap_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    imap_message_id: Mapped[str] = mapped_column(String(998), nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String(998), nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    intent: Mapped[ReplyIntent | None] = mapped_column(Enum(ReplyIntent, name="reply_intent"), nullable=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # How the intent was reached: "deterministic" (regex date/time scan) or "ai"
    # (the ambiguity model was consulted). Shown as the Discovery card's "Detection".
    intent_detection: Mapped[str | None] = mapped_column(String(32), nullable=True)

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    lead = relationship("Lead")
    matched_draft = relationship("EmailDraft")
