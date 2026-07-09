from sqlalchemy import func, select

from app.models.campaign import Campaign, CampaignStatus
from app.repositories.base import BaseRepository


class CampaignRepository(BaseRepository[Campaign]):
    model = Campaign

    def list_for_user(
        self, user_id: int, status: CampaignStatus, page: int = 1, page_size: int = 10
    ) -> tuple[list[Campaign], int]:
        base = select(Campaign).where(Campaign.user_id == user_id, Campaign.status == status)
        total = self.db.scalar(
            select(func.count()).select_from(
                select(Campaign.id).where(Campaign.user_id == user_id, Campaign.status == status).subquery()
            )
        ) or 0
        stmt = base.order_by(Campaign.updated_at.desc()).limit(page_size).offset((page - 1) * page_size)
        return list(self.db.scalars(stmt)), total
