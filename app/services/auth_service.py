"""Authentication flows: login with lockout, refresh rotation, logout, password change."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppException, ValidationFailedError
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    validate_password_strength,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.user_repository import RefreshTokenRepository, UserRepository

logger = get_logger(__name__)


class AuthError(AppException):
    status_code = 401
    code = "auth_failed"


LOCK_PREFIX = "login_attempts:"


def _check_lockout(email: str) -> None:
    try:
        attempts = get_redis().get(f"{LOCK_PREFIX}{email}")
        if attempts and int(attempts) >= settings.LOGIN_MAX_ATTEMPTS:
            raise AuthError("Too many failed attempts. Try again in a few minutes.", code="account_locked")
    except AuthError:
        raise
    except Exception as exc:  # Redis down: don't lock people out of login entirely
        logger.warning("Lockout check unavailable: %s", exc)


def _record_failure(email: str) -> None:
    try:
        r = get_redis()
        key = f"{LOCK_PREFIX}{email}"
        r.incr(key)
        r.expire(key, settings.LOGIN_LOCKOUT_SECONDS)
    except Exception:
        pass


def _clear_failures(email: str) -> None:
    try:
        get_redis().delete(f"{LOCK_PREFIX}{email}")
    except Exception:
        pass


def _issue_tokens(db: Session, user: User) -> dict:
    raw, token_hash, expires_at = generate_refresh_token()
    RefreshTokenRepository(db).create(RefreshToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    return {
        "access_token": create_access_token(user.id, user.role.value, user.organization_id),
        "refresh_token": raw,
        "token_type": "bearer",
    }


def login(db: Session, email: str, password: str) -> tuple[User, dict]:
    email = email.strip().lower()
    _check_lockout(email)

    user = UserRepository(db).get_by_email(email)
    if not user or not verify_password(password, user.hashed_password):
        _record_failure(email)
        raise AuthError("Incorrect email or password.")
    if not user.is_active:
        raise AuthError("This account has been deactivated.", code="account_disabled")

    _clear_failures(email)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user, _issue_tokens(db, user)


def refresh(db: Session, raw_refresh_token: str) -> tuple[User, dict]:
    repo = RefreshTokenRepository(db)
    token = repo.get_by_hash(hash_refresh_token(raw_refresh_token))
    now = datetime.now(timezone.utc)
    if not token or token.revoked_at is not None or token.expires_at.replace(tzinfo=timezone.utc) < now:
        raise AuthError("Session expired. Please sign in again.", code="invalid_refresh")

    user = UserRepository(db).get(token.user_id)
    if not user or not user.is_active:
        raise AuthError("This account has been deactivated.", code="account_disabled")

    token.revoked_at = now  # rotation: each refresh token is single-use
    db.commit()
    return user, _issue_tokens(db, user)


def logout(db: Session, raw_refresh_token: str | None) -> None:
    if not raw_refresh_token:
        return
    repo = RefreshTokenRepository(db)
    token = repo.get_by_hash(hash_refresh_token(raw_refresh_token))
    if token and token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.commit()


def change_password(db: Session, user: User, current_password: str, new_password: str) -> dict:
    if not verify_password(current_password, user.hashed_password):
        raise AuthError("Current password is incorrect.")
    error = validate_password_strength(new_password)
    if error:
        raise ValidationFailedError(error)
    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    # Every existing session must re-authenticate; the caller gets a fresh pair.
    RefreshTokenRepository(db).revoke_all_for_user(user.id)
    return _issue_tokens(db, user)
