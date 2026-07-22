from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.exceptions import ValidationFailedError
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    ProfileSetupRequest,
    RefreshRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.common import Message
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.organization_public_id = user.organization.public_id if user.organization else None
    return out


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    user, tokens = auth_service.login(db, payload.email, payload.password, request.headers.get("user-agent"))
    return TokenResponse(**tokens, user=_user_out(user))


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    user, tokens = auth_service.refresh(db, payload.refresh_token, request.headers.get("user-agent"))
    return TokenResponse(**tokens, user=_user_out(user))


@router.post("/logout", response_model=Message)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> Message:
    auth_service.logout(db, payload.refresh_token)
    return Message(message="Logged out.")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.post("/setup-profile", response_model=UserOut)
def setup_profile(
    payload: ProfileSetupRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UserOut:
    """Update the signed-in user's own profile. Email, role, and organization are
    identity/authorization fields — only admins change those (admin panel)."""
    user = UserRepository(db).update(user, full_name=payload.full_name.strip())
    return _user_out(user)


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    payload: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TokenResponse:
    """Requires the current password once, and the new password twice (must match) —
    the current password is verified against the stored hash in auth_service."""
    if payload.new_password != payload.confirm_new_password:
        raise ValidationFailedError("New password and confirmation do not match.")
    tokens = auth_service.change_password(db, user, payload.current_password, payload.new_password)
    return TokenResponse(**tokens, user=_user_out(user))


@router.post("/forget-password", response_model=Message)
def forget_password(payload: ForgotPasswordRequest) -> Message:
    """Stub — password reset by email ships in a later phase (no SMTP in MVP)."""
    return Message(message="If this account exists, the app owner can reset its password from the admin panel.")
