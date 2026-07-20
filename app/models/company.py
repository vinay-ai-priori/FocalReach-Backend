import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class QualificationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"  # passed gates, LLM average >= 55
    REJECTED = "rejected"  # failed a deterministic gate (geography / employee size)
    REVIEW = "review"  # passed gates, LLM average < 55 — awaiting the user's call
    REACTIVATED = "reactivated"  # manually approved out of REVIEW; treated like APPROVED downstream


class Company(Base, PublicIDMixin, TimestampMixin):
    """Canonical company record, one per (organization, domain). Firmographics come
    from the lead CSV; the enrichment fields are the org's working copy of the scraped
    profile (the cross-org TTL cache lives in global_companies). Per-campaign-run
    qualification verdicts live in CompanyQualification, never here — so re-importing
    the same CSV can never duplicate a company row."""

    __tablename__ = "companies"
    # NULLS NOT DISTINCT: the super admin's canonical companies (organization_id NULL)
    # still dedupe by domain.
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "domain", name="uq_company_org_domain", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = owned by the super admin, who sits outside every organization.
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    annual_revenue: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revenue_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    founded: Mapped[str | None] = mapped_column(String(50), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Cached crawl of the target company site, used for email personalisation
    enrichment_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured AI profile of the website (offering, ICP signals, people, news, ...)
    enrichment_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enriched_at_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_valid_till: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    qualifications = relationship("CompanyQualification", back_populates="company", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="company")


class CompanyQualification(Base, TimestampMixin):
    """Per-campaign-run qualification verdict for one canonical company: gate checks,
    LLM scores, and human overrides, scoped to the lead_import (run) they were made
    against. UNIQUE(lead_import_id, company_id) keeps one verdict per company per run."""

    __tablename__ = "company_qualifications"
    __table_args__ = (
        UniqueConstraint("lead_import_id", "company_id", name="uq_company_qualification_import_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_import_id: Mapped[int] = mapped_column(
        ForeignKey("lead_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    qualification_status: Mapped[QualificationStatus] = mapped_column(
        Enum(QualificationStatus, name="qualification_status"), default=QualificationStatus.PENDING, nullable=False
    )
    qualification_checks: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{check, result, detail}]
    qualification_override: Mapped[bool] = mapped_column(default=False, nullable=False)  # human decision applied

    # LLM qualification scores (0-100), set only for companies that pass both gates
    industry_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    company_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    qualification_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Prospect pain points the sender's offering can solve: [{pain_point, evidence, solved_by}]
    solvable_pain_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    lead_import = relationship("LeadImport", back_populates="company_qualifications")
    company = relationship("Company", back_populates="qualifications")
