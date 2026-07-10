from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class CompanyIntelligence(Base, PublicIDMixin, TimestampMixin):
    """AI-generated profile of the user's own company, derived from their website content."""

    __tablename__ = "company_intelligences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    website_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("website_analyses.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sub_industries: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    services: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{name, description}]
    business_model: Mapped[str | None] = mapped_column(String(255), nullable=True)  # B2B SaaS, services, ...
    geography: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # markets served
    company_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    technology_signals: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{signal, evidence}]
    business_signals: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    value_propositions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    target_customers: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_cached: Mapped[bool] = mapped_column(default=False, nullable=False)

    website_analysis = relationship("WebsiteAnalysis", back_populates="company_intelligence")
    icps = relationship("ICP", back_populates="company_intelligence", cascade="all, delete-orphan")
