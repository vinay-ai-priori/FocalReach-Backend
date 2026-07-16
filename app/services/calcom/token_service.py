"""Keeps a user's Cal.com access token valid. Two layers, deliberately redundant:

1. Lazy, synchronous refresh right before any API call (get_valid_access_token) — the
   request path never trusts a token that's within the buffer window of expiring, so a
   user is never blocked by a stale token even if the background job hasn't run yet.
2. A Celery beat task (app/tasks/calcom_tasks.py) that proactively refreshes every
   connection nearing expiry, so tokens get renewed even with no incoming requests.

Refreshing takes a row lock (SELECT ... FOR UPDATE) so two concurrent requests for the
same user can't both submit the same refresh_token — Cal.com invalidates the old
refresh_token on rotation, so a double-refresh race would strand the loser with a dead
token. The second request re-checks expiry after acquiring the lock and reuses the
first request's refresh instead of refreshing again.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import ExternalServiceError
from app.models.calcom_connection import CalComConnection
from app.repositories.calcom_repository import CalComConnectionRepository
from app.services.calcom.client import calcom_client

REFRESH_BUFFER = timedelta(seconds=settings.CALCOM_TOKEN_REFRESH_BUFFER_SECONDS)


def _is_due_for_refresh(connection: CalComConnection) -> bool:
    expires_at = connection.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at - REFRESH_BUFFER


def _is_permanent_refresh_failure(exc: ExternalServiceError) -> bool:
    """Only a 4xx from Cal.com means THIS token is dead (invalid_grant etc.) and the
    user must reconnect. A 5xx or a transport failure (upstream_status is None) is
    Cal.com having trouble — the connection stays alive and the next attempt retries."""
    return exc.upstream_status is not None and 400 <= exc.upstream_status < 500


def _do_refresh(db: Session, connection: CalComConnection) -> CalComConnection:
    repo = CalComConnectionRepository(db)
    refresh_token = decrypt_secret(connection.encrypted_refresh_token)
    try:
        tokens = calcom_client.refresh_token(refresh_token)
    except ExternalServiceError as exc:
        if _is_permanent_refresh_failure(exc):
            repo.update(connection, is_connected=False, last_error=str(exc))
        else:
            repo.update(connection, last_error=str(exc))
        raise
    return repo.update(
        connection,
        encrypted_access_token=encrypt_secret(tokens.access_token),
        # A refresh response without a new refresh_token means the old one is still
        # valid — keep it instead of overwriting it with something bogus.
        encrypted_refresh_token=(
            encrypt_secret(tokens.refresh_token) if tokens.refresh_token else connection.encrypted_refresh_token
        ),
        token_expires_at=tokens.expires_at,
        scope=tokens.scope or connection.scope,
        is_connected=True,
        last_error=None,
    )


def get_valid_access_token(db: Session, user_id: int) -> str:
    """Returns a Cal.com access token guaranteed to be valid for at least
    CALCOM_TOKEN_REFRESH_BUFFER_SECONDS, refreshing first if needed."""
    repo = CalComConnectionRepository(db)
    connection = repo.get_for_user(user_id)
    if not connection or not connection.is_connected:
        raise ExternalServiceError("Cal.com is not connected for this account.")

    if not _is_due_for_refresh(connection):
        return decrypt_secret(connection.encrypted_access_token)

    # Re-fetch under a row lock: another request may have refreshed this connection
    # between the check above and now.
    locked = repo.get_for_user_locked(user_id)
    if not locked or not locked.is_connected:
        raise ExternalServiceError("Cal.com is not connected for this account.")
    if not _is_due_for_refresh(locked):
        return decrypt_secret(locked.encrypted_access_token)

    refreshed = _do_refresh(db, locked)
    return decrypt_secret(refreshed.encrypted_access_token)


def refresh_if_due(db: Session, connection: CalComConnection) -> CalComConnection:
    """Used by the proactive Celery task — refreshes only if actually due, under lock."""
    if not _is_due_for_refresh(connection):
        return connection
    repo = CalComConnectionRepository(db)
    locked = repo.get_for_user_locked(connection.user_id)
    if not locked or not locked.is_connected or not _is_due_for_refresh(locked):
        return locked or connection
    return _do_refresh(db, locked)
