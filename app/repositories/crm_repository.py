from sqlalchemy import select

from app.models.crm_connection import CRMConnection, CRMProvider
from app.repositories.base import BaseRepository


class CRMConnectionRepository(BaseRepository[CRMConnection]):
    model = CRMConnection

    def get_by_provider(self, organization_id: int | None, provider: CRMProvider) -> CRMConnection | None:
        """Org-scoped: one org's connection is never visible to another.
        NULL organization = the super admin's own connection."""
        org_filter = (
            CRMConnection.organization_id.is_(None)
            if organization_id is None
            else CRMConnection.organization_id == organization_id
        )
        return self.db.scalar(
            select(CRMConnection).where(org_filter, CRMConnection.provider == provider)
        )
