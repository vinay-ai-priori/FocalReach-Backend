from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.company import QualificationStatus


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    lead_import_id: int
    name: str
    website: str | None = None
    domain: str | None = None
    industry: str | None = None
    description: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    employee_count: int | None = None
    employee_range: str | None = None
    annual_revenue: str | None = None
    revenue_range: str | None = None
    qualification_status: QualificationStatus
    qualification_checks: list | None = None
    qualification_override: bool = False


class QualificationDecision(BaseModel):
    status: QualificationStatus  # approved | rejected


class QualificationSummary(BaseModel):
    total: int
    approved: int
    rejected: int
    review: int
    pending: int
