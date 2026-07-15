from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.email_draft import DraftChannel, DraftStatus


class EmailDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    lead_public_id: UUID | None = None
    channel: DraftChannel = DraftChannel.EMAIL
    step_index: int = 1
    status: DraftStatus
    subject: str | None = None
    body: str | None = None
    personalization_notes: str | None = None
    booking_link: str | None = None
    ai_cached: bool = False
    error_message: str | None = None
    last_test_email: str | None = None
    refine_count: int = 0
    sent_at: datetime | None = None
    scheduled_at: datetime | None = None
    attempt_count: int = 0
    created_at: datetime


class EmailDraftUpdate(BaseModel):
    subject: str | None = None
    body: str | None = None


class SendTestRequest(BaseModel):
    email: EmailStr


class DispatchResolveRequest(BaseModel):
    """Resolution for a NEEDS_ATTENTION draft: the user checked their Sent folder and
    tells us whether the interrupted dispatch actually went out."""

    resolution: str = Field(pattern="^(mark_sent|retry)$")


class DraftBatchRequest(BaseModel):
    lead_ids: list[UUID] | None = None  # None = all eligible leads in the import


class StepCreateRequest(BaseModel):
    """Generate the next outreach step for a lead. For channel=email the next follow-up
    slot (2-4) is computed server-side; linkedin/call occupy their fixed positions."""

    channel: DraftChannel


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    kind: str
    due_step_index: int
    lead_public_id: UUID | None = None
    lead_name: str | None = None
    company_name: str | None = None
    campaign_public_id: UUID | None = None
    read_at: datetime | None = None
    created_at: datetime


class DraftRefineRequest(BaseModel):
    mode: str = Field(
        pattern="^(regenerate|shorter|more_technical|more_executive|more_friendly|personalize_further)$"
    )
