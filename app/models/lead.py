import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class LeadTier(str, enum.Enum):
    HOT = "hot"
    WARM = "warm"
    NURTURE = "nurture"
    DEPRIORITIZED = "deprioritized"


class Lead(Base, PublicIDMixin, TimestampMixin):
    """An individual prospect from the CSV, scored deterministically against the ICP."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_import_id: Mapped[int] = mapped_column(
        ForeignKey("lead_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    email_validation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Cached result of lead_timezone_service, derived from `country` — populated the
    # first time GET /leads/{id}/timezone is called so repeat lookups skip pycountry/pytz.
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    time_in_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    time_at_company: Mapped[str | None] = mapped_column(String(100), nullable=True)
    years_experience: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Cross-campaign deduplication (see dedup_service). A lead is flagged as a duplicate
    # when the same contact is already ACTIVE in another of the owner's campaigns; flagged
    # leads are excluded from scoring and outreach but kept for auditability.
    is_duplicate: Mapped[bool] = mapped_column(default=False, nullable=False)
    duplicate_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Holds the whole outreach sequence for this lead (initial email + all future
    # touches) without changing anything already drafted — reversible, unlike draft
    # approval. Blocks Send/Send Test/Approve/refine while set.
    outreach_paused: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Scoring per the Role Score & Signal Score logic document:
    # role_score 0-30 (title tier + size modifier), signal_score 0-25 (tenure + experience),
    # company_fit_score 0-30 (inherited from company qualification), total_score 0-85.
    # industry_score/fit_score are legacy columns from the old 0-100 scheme, kept for data.
    industry_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    role_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    company_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier: Mapped[LeadTier | None] = mapped_column(Enum(LeadTier, name="lead_tier"), nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    lead_import = relationship("LeadImport", back_populates="leads")
    company = relationship("Company", back_populates="leads")
    email_drafts = relationship("EmailDraft", back_populates="lead", cascade="all, delete-orphan")
