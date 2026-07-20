from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class ICP(Base, PublicIDMixin, TimestampMixin):
    """Ideal Customer Profile. AI-generated from company intelligence, then
    user-editable. A campaign artifact: owned via campaign → user → org (no duplicated
    ownership columns). Versions are retained; exactly one may be active per campaign."""

    __tablename__ = "icps"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version", name="uq_icp_campaign_version"),
        Index("ux_icps_campaign_active", "campaign_id", unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_intelligence_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_intelligences.id", ondelete="SET NULL"), nullable=True, index=True
    )

    campaign_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The AI-generated candidates campaign_objective was chosen from (empty once fully
    # hand-written).
    campaign_objective_options: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    target_industries: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    company_size_ranges: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)  # [{min, max, label}]
    target_roles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    target_keywords: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    target_seniorities: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    target_geographies: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    outreach_tone: Mapped[str] = mapped_column(String(50), default="consultative", nullable=False)

    is_ai_generated: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    campaign = relationship("Campaign", back_populates="icps")
    company_intelligence = relationship("CompanyIntelligence", back_populates="icps")
