from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BookingOut(BaseModel):
    public_id: UUID
    status: str
    resolved_start: datetime | None = None
    resolved_timezone: str | None = None
    timezone_source: str | None = None
    last_error: str | None = None
    calcom_booking_uid: str | None = None
    meeting_url: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    lead_public_id: UUID | None = None
    lead_name: str | None = None
    lead_email: str | None = None
    company_name: str | None = None
    reply_subject: str | None = None
    reply_excerpt: str | None = None


class ManualBookRequest(BaseModel):
    # ISO-8601 slot start exactly as returned by GET /calcom/slots/day.
    start: str = Field(min_length=1)
