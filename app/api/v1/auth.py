from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.common import Message
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user, tokens = auth_service.login(db, payload.email, payload.password)
    return TokenResponse(**tokens, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user, tokens = auth_service.refresh(db, payload.refresh_token)
    return TokenResponse(**tokens, user=UserOut.model_validate(user))


@router.post("/logout", response_model=Message)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> Message:
    auth_service.logout(db, payload.refresh_token)
    return Message(message="Logged out.")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    payload: ChangePasswordRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TokenResponse:
    tokens = auth_service.change_password(db, user, payload.current_password, payload.new_password)
    return TokenResponse(**tokens, user=UserOut.model_validate(user))


@router.post("/forget-password", response_model=Message)
def forget_password(payload: ForgotPasswordRequest) -> Message:
    """Stub — password reset by email ships in a later phase (no SMTP in MVP)."""
    return Message(message="If this account exists, the app owner can reset its password from the admin panel.")
