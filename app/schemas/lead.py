from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.lead import LeadTier


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    lead_import_id: int
    company_id: int
    company_name: str | None = None
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    seniority: str | None = None
    department: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    city: str | None = None
    country: str | None = None
    time_in_role: str | None = None
    time_at_company: str | None = None
    industry_score: float | None = None
    role_score: float | None = None
    fit_score: float | None = None
    total_score: float | None = None
    tier: LeadTier | None = None
    score_breakdown: dict | None = None


class PrioritizationSummary(BaseModel):
    total: int
    hot: int
    warm: int
    nurture: int
    deprioritized: int
