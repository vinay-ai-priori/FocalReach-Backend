from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.email_draft import DraftStatus


class EmailDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    lead_id: int
    status: DraftStatus
    subject: str | None = None
    body: str | None = None
    personalization_notes: str | None = None
    booking_link: str | None = None
    ai_cached: bool = False
    error_message: str | None = None
    created_at: datetime


class EmailDraftUpdate(BaseModel):
    subject: str | None = None
    body: str | None = None


class DraftBatchRequest(BaseModel):
    lead_ids: list[int] | None = None  # None = all hot+warm leads in the import
