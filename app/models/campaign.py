import enum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class CampaignStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Campaign(Base, PublicIDMixin, TimestampMixin):
    """A single outbound campaign — the aggregate root that ties together one run of
    the flow (website analysis → company intelligence → ICP → lead import). Private to
    its creating user; the org is reached through the user (single ownership path).

    Children (icps, lead_imports) point UP to the campaign. The two pointers kept here
    (website_analysis_id, company_intelligence_id) reference org-level cached artifacts
    that are shared across campaigns, so the campaign must hold the pointer.
    `stage` is derived on read from which artifacts exist + the lead-import status."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # set on the ICP builder page
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"), default=CampaignStatus.ACTIVE, nullable=False, index=True
    )

    # Pointers to org-level shared/cached artifacts.
    website_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("website_analyses.id", ondelete="SET NULL"), nullable=True
    )
    company_intelligence_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_intelligences.id", ondelete="SET NULL"), nullable=True
    )

    user = relationship("User")
    website_analysis = relationship("WebsiteAnalysis")
    company_intelligence = relationship("CompanyIntelligence")
    icps = relationship("ICP", back_populates="campaign", cascade="all, delete-orphan")
    lead_imports = relationship("LeadImport", back_populates="campaign", cascade="all, delete-orphan")
