from sqlalchemy import select

from app.models.company import Company, QualificationStatus
from app.repositories.base import BaseRepository


class CompanyRepository(BaseRepository[Company]):
    model = Company

    def list_for_import(self, lead_import_id: int, status: QualificationStatus | None = None) -> list[Company]:
        stmt = select(Company).where(Company.lead_import_id == lead_import_id)
        if status:
            stmt = stmt.where(Company.qualification_status == status)
        stmt = stmt.order_by(Company.name)
        return list(self.db.scalars(stmt))
