"""Proactive Cal.com token refresh — belt-and-suspenders alongside the lazy refresh in
app/services/calcom/token_service.py, so tokens get renewed even for users who aren't
actively hitting the API right now."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.calcom_connection import CalComConnection
from app.services.calcom.token_service import refresh_if_due

logger = get_logger(__name__)


@celery_app.task(name="calcom.refresh_expiring_tokens")
def refresh_expiring_tokens() -> None:
    db = SessionLocal()
    try:
        # Same window the lazy path uses, widened a bit so this sweep catches anything
        # about to fall inside the buffer before the next scheduled run.
        horizon = datetime.now(timezone.utc) + timedelta(minutes=15)
        stmt = select(CalComConnection).where(
            CalComConnection.is_connected.is_(True), CalComConnection.token_expires_at <= horizon
        )
        due = list(db.scalars(stmt))
        for connection in due:
            try:
                refresh_if_due(db, connection)
            except Exception:
                logger.exception("Cal.com proactive refresh failed for user_id=%s", connection.user_id)
        if due:
            logger.info("Cal.com proactive refresh: processed %d connection(s)", len(due))
    finally:
        db.close()
