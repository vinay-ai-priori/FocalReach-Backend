from sqlalchemy import select

from app.models.company_intelligence import CompanyIntelligence
from app.repositories.base import BaseRepository


class CompanyIntelligenceRepository(BaseRepository[CompanyIntelligence]):
    model = CompanyIntelligence

    def get_by_analysis(self, website_analysis_id: int) -> CompanyIntelligence | None:
        return self.db.scalar(
            select(CompanyIntelligence).where(CompanyIntelligence.website_analysis_id == website_analysis_id)
        )
