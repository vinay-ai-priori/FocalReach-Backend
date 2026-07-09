from sqlalchemy import select

from app.models.crm_connection import CRMConnection, CRMProvider
from app.repositories.base import BaseRepository


class CRMConnectionRepository(BaseRepository[CRMConnection]):
    model = CRMConnection

    def get_by_provider(self, provider: CRMProvider) -> CRMConnection | None:
        return self.db.scalar(select(CRMConnection).where(CRMConnection.provider == provider))
