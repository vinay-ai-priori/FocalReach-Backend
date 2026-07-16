import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class MailboxProvider(str, enum.Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"


class MailboxConnection(Base, PublicIDMixin, TimestampMixin):
    """A user's own mailbox, connected via IMAP/SMTP with an app password — used to
    send outreach from, and (later) read replies into, their real inbox. Per-user:
    each rep sends from their own address, never a shared/global connection."""

    __tablename__ = "mailbox_connections"
    __table_args__ = (UniqueConstraint("user_id", "email_address", name="uq_mailbox_user_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    provider: Mapped[MailboxProvider] = mapped_column(Enum(MailboxProvider, name="mailbox_provider"), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)

    imap_host: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)

    # Fernet-encrypted app password (app/core/crypto.py) — never stored or returned
    # in plaintext.
    encrypted_app_password: Mapped[str] = mapped_column(Text, nullable=False)

    is_connected: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_verification_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Inbox reply poller cursor (app/services/inbox/imap_poll_service.py). UIDVALIDITY
    # scopes last_polled_uid — if the server-reported value changes, the mailbox's UIDs
    # were reassigned and the cursor must reset (treated as a fresh mailbox) or every
    # UID comparison after that point is meaningless.
    imap_uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_polled_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
