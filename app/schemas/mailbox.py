from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.mailbox_connection import MailboxProvider


class MailboxProviderOut(BaseModel):
    provider: MailboxProvider
    display_name: str
    app_password_url: str
    instructions: list[str]


class MailboxConnectRequest(BaseModel):
    provider: MailboxProvider
    email: EmailStr
    app_password: str = Field(min_length=1, max_length=512)


class MailboxConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    provider: MailboxProvider
    email_address: str
    is_connected: bool
    last_verification_error: str | None = None
    created_at: datetime
