from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.auth import UserOut


# ---------- Tenants ----------
class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    criteria: dict = {}


class TenantUpdate(BaseModel):
    name: str | None = None
    criteria: dict | None = None
    is_active: bool | None = None


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    name: str
    criteria: dict
    is_active: bool
    created_at: datetime
    organization_count: int = 0


# ---------- Organizations ----------
class OrganizationCreate(BaseModel):
    tenant_id: int
    name: str = Field(min_length=2, max_length=255)


class OrganizationUpdate(BaseModel):
    name: str | None = None
    tenant_id: int | None = None
    is_active: bool | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: UUID
    tenant_id: int
    name: str
    is_active: bool
    created_at: datetime
    user_count: int = 0
    tenant_name: str | None = None


# ---------- Users ----------
class UserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8)
    organization_id: int


class UserUpdate(BaseModel):
    full_name: str | None = None
    organization_id: int | None = None
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=8)


class AdminUserOut(UserOut):
    organization_name: str | None = None
