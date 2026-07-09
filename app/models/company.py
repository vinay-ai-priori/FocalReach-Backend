import enum

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class QualificationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVIEW = "review"


class Company(Base, TimestampMixin):
    """A target company extracted from a lead CSV, qualified against the active ICP."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_import_id: Mapped[int] = mapped_column(
        ForeignKey("lead_imports.id", ondelete="CASCADE"), nullable=False, index=True
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

    qualification_status: Mapped[QualificationStatus] = mapped_column(
        Enum(QualificationStatus, name="qualification_status"), default=QualificationStatus.PENDING, nullable=False
    )
    qualification_checks: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{check, result, detail}]
    qualification_override: Mapped[bool] = mapped_column(default=False, nullable=False)  # human decision applied

    # Cached crawl of the target company site, used for email personalisation
    enrichment_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at_status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    lead_import = relationship("LeadImport", back_populates="companies")
    leads = relationship("Lead", back_populates="company", cascade="all, delete-orphan")
