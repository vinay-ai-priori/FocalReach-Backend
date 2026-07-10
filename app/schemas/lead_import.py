from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.lead_import import ImportStatus


class FieldMapping(BaseModel):
    canonical_field: str
    label: str
    csv_column: str | None = None
    confidence: float = 0.0
    required_for: str | None = None  # company_qualification | lead_qualification | None
    is_mandatory: bool = False


class MissingFieldWarning(BaseModel):
    canonical_field: str
    label: str
    severity: str  # critical | warning
    required_for: str | None = None
    consequence: str


class ColumnMappingUpdate(BaseModel):
    column_mapping: dict[str, str | None]


class LeadImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    icp_id: int
    filename: str
    status: ImportStatus
    total_rows: int
    raw_columns: list
    column_mapping: dict
    missing_fields: list
    error_message: str | None = None
    created_at: datetime


class ImportStats(BaseModel):
    rows_detected: int
    columns_detected: int
    unique_companies: int
    total_leads: int  # rows that will actually be imported (after drops)
    # Rows dropped at import (missing company name, name, or email)
    rows_dropped: int
    dropped_missing_company: int
    dropped_missing_name: int
    dropped_missing_email: int
    # Cross-campaign deduplication (organization-scoped)
    already_targeted_companies: int
    duplicate_active_leads: int
    net_new_leads: int


class ImportValidationOut(BaseModel):
    lead_import: LeadImportOut
    field_mappings: list[FieldMapping]
    missing_fields: list[MissingFieldWarning]
    sample_rows: list[dict]
    stats: ImportStats
