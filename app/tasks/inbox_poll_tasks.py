"""Inbox reply poller (Celery beat, every 10 minutes).

For each connected mailbox: fetch new IMAP messages, match them to leads, classify
intent, and route (app/services/inbox/{imap_poll_service,intent_classifier,
reply_router}.py). A Redis lock guards against overlap if one run takes longer than
the 10-minute interval (e.g. a slow IMAP server) — the next tick just skips rather
than piling up concurrent polls of the same mailboxes.
"""

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.core.redis_client import get_redis
from app.db.session import SessionLocal
from app.models.mailbox_connection import MailboxConnection
from app.services.inbox.imap_poll_service import poll_mailbox
from app.services.inbox.reply_router import route_reply

configure_logging()
logger = get_logger(__name__)

POLL_LOCK_KEY = "inbox:poll:lock"
POLL_LOCK_TTL_SECONDS = 540  # under the 10-minute beat interval


@celery_app.task(name="inbox.poll_replies")
def poll_replies() -> dict:
    redis = get_redis()
    if not redis.set(POLL_LOCK_KEY, "1", nx=True, ex=POLL_LOCK_TTL_SECONDS):
        logger.info("inbox.poll_replies already running — skipping this tick")
        return {"skipped": True}

    db = SessionLocal()
    polled, matched, errors = 0, 0, 0
    try:
        mailboxes = list(
            db.scalars(select(MailboxConnection).where(MailboxConnection.is_connected.is_(True)))
        )
        for mailbox in mailboxes:
            try:
                new_replies = poll_mailbox(db, mailbox)
                polled += 1
            except Exception:
                logger.exception("IMAP poll failed for mailbox %s", mailbox.email_address)
                errors += 1
                db.rollback()
                continue

            for reply in new_replies:
                try:
                    route_reply(db, reply)
                    matched += 1
                except Exception:
                    logger.exception("Failed to route inbound reply %s", reply.id)
                    db.rollback()

        return {"mailboxes_polled": polled, "replies_routed": matched, "errors": errors}
    finally:
        db.close()
        try:
            redis.delete(POLL_LOCK_KEY)
        except Exception:
            pass
