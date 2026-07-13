from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class ICP(Base, PublicIDMixin, TimestampMixin):
    """Ideal Customer Profile. AI-generated from company intelligence, then user-editable."""

    __tablename__ = "icps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_intelligence_id: Mapped[int] = mapped_column(
        ForeignKey("company_intelligences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ICPs are campaign artifacts: private to their creating user.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    campaign_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The AI-generated candidates campaign_objective was chosen from (empty for
    # ICPs created before this field existed, or once fully hand-written).
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

    company_intelligence = relationship("CompanyIntelligence", back_populates="icps")
    lead_imports = relationship("LeadImport", back_populates="icp", cascade="all, delete-orphan")
