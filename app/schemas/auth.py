from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ProfileSetupRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    email: str
    full_name: str
    role: UserRole
    organization_public_id: UUID | None = None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut
