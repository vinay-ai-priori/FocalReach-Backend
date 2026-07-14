from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.lead import LeadTier


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    lead_import_public_id: UUID | None = None
    company_public_id: UUID | None = None
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
    timezone: str | None = None
    time_in_role: str | None = None
    time_at_company: str | None = None
    years_experience: str | None = None
    industry_score: float | None = None
    role_score: float | None = None
    fit_score: float | None = None
    signal_score: float | None = None
    company_fit_score: float | None = None
    total_score: float | None = None
    tier: LeadTier | None = None
    score_breakdown: dict | None = None
    outreach_paused: bool = False


class PrioritizationSummary(BaseModel):
    total: int
    hot: int
    warm: int
    nurture: int
    deprioritized: int


class LeadTimezoneOut(BaseModel):
    country: str
    country_code: str | None = None
    timezone: str | None = None
