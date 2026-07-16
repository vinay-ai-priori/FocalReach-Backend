from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin

DEFAULT_WORKING_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


class CalComConnection(Base, PublicIDMixin, TimestampMixin):
    """A user's Cal.com account, connected via OAuth2 — one per user. Access/refresh
    tokens are Fernet-encrypted at rest (app/core/crypto.py) and are refreshed
    automatically, both lazily on use (app/services/calcom/token_service.py) and
    proactively by a Celery beat task, so they should never be observed expired."""

    __tablename__ = "calcom_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    calcom_user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    calcom_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(512), nullable=True)

    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    selected_event_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_event_type_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_event_type_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Working hours, mirrored to a Cal.com "Schedule" (a concept separate from event
    # types — event types just point at a scheduleId). Cached here too so the app has
    # a fast, always-available read of a user's hours without round-tripping Cal.com
    # (e.g. for future scheduling/booking logic that needs to reason about availability
    # without an extra API call).
    working_days: Mapped[list] = mapped_column(JSONB, default=lambda: list(DEFAULT_WORKING_DAYS), nullable=False)
    working_hours_start: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    working_hours_end: Mapped[str] = mapped_column(String(5), default="17:00", nullable=False)
    # The Cal.com Schedule this maps to — created on first "Save working hours", then
    # updated in place afterward. New event types are created pointing at this schedule.
    calcom_schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calcom_schedule_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_connected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    user = relationship("User")
