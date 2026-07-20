from sqlalchemy import select

from app.models.icp import ICP
from app.repositories.base import BaseRepository


class ICPRepository(BaseRepository[ICP]):
    model = ICP

    def get_active_for_campaign(self, campaign_id: int) -> ICP | None:
        """ICPs are campaign artifacts — at most one active per campaign (DB-enforced)."""
        stmt = (
            select(ICP)
            .where(ICP.campaign_id == campaign_id, ICP.is_active.is_(True))
            .order_by(ICP.version.desc())
        )
        return self.db.scalars(stmt).first()
