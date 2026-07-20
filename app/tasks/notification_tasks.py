"""Follow-up-due notifications (Celery beat, hourly).

Scans every lead whose LATEST sent email step has gone unanswered past its cadence
window and raises an in-app notification (the campaign header bell). Cadence, keyed
by the follow-up that would come next: follow-up 1 fires 3 days after the initial
email was dispatched, follow-up 2 fires 4 days after follow-up 1, follow-up 3 fires
7 days after follow-up 2 (FOLLOW_UP_DUE_DAYS on the model).

Strictly a nudge: nothing is generated or sent automatically — clicking the
notification only routes the user to the lead. The partial unique index
ux_notifications_lead_kind_unread makes the scan idempotent (at most one unread
nudge per lead), so it can run blindly every hour.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.models.email_draft import (
    FOLLOW_UP_DUE_DAYS,
    STEP_FOLLOW_UP_LAST,
    DraftChannel,
    DraftStatus,
    EmailDraft,
)
from app.models.lead import Lead
from app.models.notification import Notification

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="outreach.raise_follow_up_due")
def raise_follow_up_due_task() -> dict:
    db = SessionLocal()
    raised = 0
    try:
        now = datetime.now(timezone.utc)

        # Per lead: the highest SENT email step (the last touch that actually went out).
        latest_sent = (
            select(EmailDraft.lead_id, func.max(EmailDraft.step_index).label("last_step"))
            .where(
                EmailDraft.channel == DraftChannel.EMAIL,
                EmailDraft.status == DraftStatus.SENT,
                EmailDraft.sent_at.is_not(None),
            )
            .group_by(EmailDraft.lead_id)
            .subquery()
        )
        rows = db.execute(
            select(EmailDraft, Lead)
            .join(
                latest_sent,
                (EmailDraft.lead_id == latest_sent.c.lead_id)
                & (EmailDraft.step_index == latest_sent.c.last_step),
            )
            .join(Lead, EmailDraft.lead_id == Lead.id)
            .where(
                EmailDraft.channel == DraftChannel.EMAIL,
                EmailDraft.status == DraftStatus.SENT,
                EmailDraft.step_index < STEP_FOLLOW_UP_LAST + 1,
                Lead.outreach_paused.is_(False),
            )
        ).all()

        for sent_draft, lead in rows:
            next_step = sent_draft.step_index + 1
            due_days = FOLLOW_UP_DUE_DAYS.get(next_step)
            if due_days is None or sent_draft.sent_at is None:
                continue
            if now - sent_draft.sent_at < timedelta(days=due_days):
                continue
            # Skip if the next follow-up is already drafted (any non-failed state).
            already = db.scalars(
                select(EmailDraft.id).where(
                    EmailDraft.lead_id == lead.id,
                    EmailDraft.channel == DraftChannel.EMAIL,
                    EmailDraft.step_index == next_step,
                    EmailDraft.status != DraftStatus.FAILED,
                )
            ).first()
            if already:
                continue
            user_id = lead.lead_import.campaign.user_id if lead.lead_import else None
            if user_id is None:
                continue
            try:
                db.add(
                    Notification(
                        user_id=user_id, lead_id=lead.id, kind="follow_up_due", due_step_index=next_step
                    )
                )
                db.commit()
                raised += 1
            except IntegrityError:
                db.rollback()  # an unread nudge for this lead already exists

        return {"raised": raised, "checked": len(rows)}
    finally:
        db.close()
