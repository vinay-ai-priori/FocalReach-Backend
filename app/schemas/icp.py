from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ICPGenerateRequest(BaseModel):
    company_intelligence_id: UUID
    campaign_id: UUID | None = None


class ICPUpdateRequest(BaseModel):
    campaign_objective: str | None = None
    campaign_objective_options: list[str] | None = None
    target_industries: list[str] | None = None
    company_size_ranges: list[dict] | None = None
    target_roles: list[str] | None = None
    target_keywords: list[str] | None = None
    target_seniorities: list[str] | None = None
    target_geographies: list[str] | None = None
    outreach_tone: str | None = Field(default=None, pattern="^(consultative|direct|formal)$")


class ICPOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    company_intelligence_public_id: UUID | None = None
    campaign_objective: str | None = None
    campaign_objective_options: list[str] = []
    target_industries: list
    company_size_ranges: list
    target_roles: list
    target_keywords: list
    target_seniorities: list
    target_geographies: list
    outreach_tone: str
    is_ai_generated: bool
    version: int
    created_at: datetime
    updated_at: datetime
