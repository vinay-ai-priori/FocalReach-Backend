from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.campaign import CampaignStatus


class CampaignCreate(BaseModel):
    url: str
    force_refresh: bool = False


class CampaignUpdate(BaseModel):
    name: str | None = None
    status: CampaignStatus | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    name: str | None = None
    status: CampaignStatus
    stage: str = "website"  # derived
    website_analysis_public_id: UUID | None = None
    company_intelligence_public_id: UUID | None = None
    icp_public_id: UUID | None = None
    lead_import_public_id: UUID | None = None
    analysis_status: str | None = None
    import_status: str | None = None
    # True when the ICP was edited after this campaign's results were computed.
    results_stale: bool = False
    created_at: datetime
    updated_at: datetime


class CampaignListOut(BaseModel):
    items: list[CampaignOut]
    total: int
    page: int
    page_size: int
