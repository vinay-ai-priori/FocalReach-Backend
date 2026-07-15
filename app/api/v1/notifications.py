from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.exceptions import NotFoundError
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.lead_import import LeadImport
from app.models.notification import Notification
from app.models.user import User
from app.schemas.email import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(notification: Notification, lead: Lead, campaign: Campaign | None) -> NotificationOut:
    out = NotificationOut.model_validate(notification)
    out.lead_public_id = lead.public_id
    out.lead_name = lead.full_name
    out.company_name = lead.company.name if lead.company else None
    out.campaign_public_id = campaign.public_id if campaign else None
    return out


@router.get("", response_model=list[NotificationOut])
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[NotificationOut]:
    """The bell's contents: this user's UNREAD follow-up-due nudges, newest first, each
    carrying the lead + campaign ids the frontend needs to route straight to the lead."""
    rows = db.execute(
        select(Notification, Lead, Campaign)
        .join(Lead, Notification.lead_id == Lead.id)
        .join(LeadImport, Lead.lead_import_id == LeadImport.id)
        .outerjoin(Campaign, Campaign.lead_import_id == LeadImport.id)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .order_by(Notification.id.desc())
    ).all()
    return [_out(n, lead, campaign) for n, lead, campaign in rows]


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> NotificationOut:
    notification = db.scalars(
        select(Notification).where(Notification.public_id == notification_id, Notification.user_id == user.id)
    ).first()
    if not notification:
        raise NotFoundError(f"Notification {notification_id} not found.")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
    lead = notification.lead
    campaign = db.scalars(select(Campaign).where(Campaign.lead_import_id == lead.lead_import_id)).first()
    return _out(notification, lead, campaign)
