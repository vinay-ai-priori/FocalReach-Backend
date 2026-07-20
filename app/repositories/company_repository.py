from sqlalchemy import select

from app.models.company import Company, CompanyQualification, QualificationStatus
from app.repositories.base import BaseRepository


class CompanyRepository(BaseRepository[Company]):
    model = Company

    def list_for_import(
        self, lead_import_id: int, status: QualificationStatus | None = None
    ) -> list[tuple[CompanyQualification, Company]]:
        """(qualification, company) pairs for one run, ordered by company name."""
        stmt = (
            select(CompanyQualification, Company)
            .join(Company, CompanyQualification.company_id == Company.id)
            .where(CompanyQualification.lead_import_id == lead_import_id)
        )
        if status:
            stmt = stmt.where(CompanyQualification.qualification_status == status)
        stmt = stmt.order_by(Company.name)
        return [(q, c) for q, c in self.db.execute(stmt)]

    def qualification_for(self, lead_import_id: int, company_id: int) -> CompanyQualification | None:
        return self.db.scalars(
            select(CompanyQualification).where(
                CompanyQualification.lead_import_id == lead_import_id,
                CompanyQualification.company_id == company_id,
            )
        ).first()

    def qualifications_by_company(self, lead_import_id: int) -> dict[int, CompanyQualification]:
        return {
            q.company_id: q
            for q in self.db.scalars(
                select(CompanyQualification).where(CompanyQualification.lead_import_id == lead_import_id)
            )
        }
