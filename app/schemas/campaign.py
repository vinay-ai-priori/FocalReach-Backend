from datetime import datetime

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

    id: int
    name: str | None = None
    status: CampaignStatus
    stage: str = "website"  # derived
    website_analysis_id: int | None = None
    company_intelligence_id: int | None = None
    icp_id: int | None = None
    lead_import_id: int | None = None
    analysis_status: str | None = None
    import_status: str | None = None
    created_at: datetime
    updated_at: datetime


class CampaignListOut(BaseModel):
    items: list[CampaignOut]
    total: int
    page: int
    page_size: int
