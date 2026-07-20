import enum

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class AnalysisStatus(str, enum.Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    SCRAPING = "scraping"
    EXTRACTING = "extracting"
    GENERATING_INTELLIGENCE = "generating_intelligence"
    COMPLETED = "completed"
    FAILED = "failed"


class WebsiteAnalysis(Base, PublicIDMixin, TimestampMixin):
    """One analysis per domain *per organization*. Cached: re-submitting the same domain
    within the same org reuses the row."""

    __tablename__ = "website_analyses"
    # NULLS NOT DISTINCT: the super admin's rows (organization_id NULL) still dedupe
    # by domain — without it Postgres would treat every NULL as unique.
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "domain", name="uq_analysis_org_domain", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = owned by the super admin, who sits outside every organization.
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus, name="analysis_status"), default=AnalysisStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scrape results
    page_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawled_pages: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{url, title, chars}]
    used_playwright: Mapped[bool] = mapped_column(default=False, nullable=False)
    scrape_engine: Mapped[str | None] = mapped_column(String(50), nullable=True)  # httpx | playwright

    company_intelligence = relationship(
        "CompanyIntelligence", back_populates="website_analysis", uselist=False, cascade="all, delete-orphan"
    )
