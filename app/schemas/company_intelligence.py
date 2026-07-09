from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CompanyIntelligenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    website_analysis_id: int
    company_name: str | None = None
    summary: str | None = None
    industry: str | None = None
    sub_industries: list | None = None
    services: list | None = None
    business_model: str | None = None
    geography: list | None = None
    company_size: str | None = None
    technology_signals: list | None = None
    business_signals: list | None = None
    value_propositions: list | None = None
    target_customers: list | None = None
    ai_model: str | None = None
    ai_cached: bool = False
    created_at: datetime
