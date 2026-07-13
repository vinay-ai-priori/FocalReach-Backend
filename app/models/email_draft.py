import enum

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class DraftStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


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

    lead = relationship("Lead", back_populates="email_drafts")
