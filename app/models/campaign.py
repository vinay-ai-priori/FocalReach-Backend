import enum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicIDMixin, TimestampMixin


class CampaignStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Campaign(Base, PublicIDMixin, TimestampMixin):
    """A single outbound campaign — the aggregate that ties together one run of the flow
    (website analysis → company intelligence → ICP → lead import). Private to its creating
    user. `stage` is derived on read from which artifacts exist + the lead-import status."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # set on the ICP builder page
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"), default=CampaignStatus.ACTIVE, nullable=False, index=True
    )

    # Flow artifacts, filled in as the campaign progresses.
    website_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("website_analyses.id", ondelete="SET NULL"), nullable=True
    )
    company_intelligence_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_intelligences.id", ondelete="SET NULL"), nullable=True
    )
    icp_id: Mapped[int | None] = mapped_column(ForeignKey("icps.id", ondelete="SET NULL"), nullable=True)
    lead_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("lead_imports.id", ondelete="SET NULL"), nullable=True
    )

    website_analysis = relationship("WebsiteAnalysis")
    company_intelligence = relationship("CompanyIntelligence")
    icp = relationship("ICP")
    # Two FK paths exist between campaigns and lead_imports (this one, plus
    # lead_imports.campaign_id used by pending re-uploads) — pin the FK explicitly.
    lead_import = relationship("LeadImport", foreign_keys=[lead_import_id])
