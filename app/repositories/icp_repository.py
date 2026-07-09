from sqlalchemy import select

from app.models.icp import ICP
from app.repositories.base import BaseRepository


class ICPRepository(BaseRepository[ICP]):
    model = ICP

    def get_active_for_intelligence(self, company_intelligence_id: int, user_id: int | None = None) -> ICP | None:
        """ICPs are per-user campaign artifacts: two colleagues get independent ICPs."""
        stmt = (
            select(ICP)
            .where(
                ICP.company_intelligence_id == company_intelligence_id,
                ICP.is_active.is_(True),
                ICP.user_id == user_id,
            )
            .order_by(ICP.version.desc())
        )
        return self.db.scalars(stmt).first()
