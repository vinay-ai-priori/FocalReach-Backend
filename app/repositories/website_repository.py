from sqlalchemy import select

from app.models.website_analysis import WebsiteAnalysis
from app.repositories.base import BaseRepository


class WebsiteAnalysisRepository(BaseRepository[WebsiteAnalysis]):
    model = WebsiteAnalysis

    def get_by_domain(self, domain: str, organization_id: int | None = None) -> WebsiteAnalysis | None:
        """Analysis cache is per-organization: same domain in another org is a separate row.
        NULL organization = the super admin's own space (== comparison never matches NULL)."""
        stmt = select(WebsiteAnalysis).where(WebsiteAnalysis.domain == domain)
        stmt = stmt.where(
            WebsiteAnalysis.organization_id.is_(None)
            if organization_id is None
            else WebsiteAnalysis.organization_id == organization_id
        )
        return self.db.scalar(stmt)
