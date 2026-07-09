"""RBAC dependencies: role gates the endpoint, organization scopes the data."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.exceptions import AppException
from app.core.security import decode_access_token
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository


class Unauthorized(AppException):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppException):
    status_code = 403
    code = "forbidden"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise Unauthorized("Not authenticated.")
    payload = decode_access_token(auth.removeprefix("Bearer ").strip())
    if not payload:
        raise Unauthorized("Session expired or invalid token.")
    user = UserRepository(db).get(int(payload["sub"]))
    if not user or not user.is_active:
        raise Unauthorized("Account not found or deactivated.")
    return user


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.SUPER_ADMIN:
        raise Forbidden("Administration panel is restricted to the super admin.")
    return user
