from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models.company import Company, QualificationStatus
from app.models.lead import Lead, LeadTier
from app.repositories.base import BaseRepository


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    def list_for_import(self, lead_import_id: int, tier: LeadTier | None = None) -> list[Lead]:
        # The working set for prioritization/outreach is leads of APPROVED companies only
        # (leads of rejected / needs-review companies are never scored or contacted).
        # Deduplicated leads are also excluded (kept in DB for auditing).
        stmt = (
            select(Lead)
            .join(Company, Lead.company_id == Company.id)
            .options(joinedload(Lead.company))  # avoid N+1 when reading company_name
            .where(
                Lead.lead_import_id == lead_import_id,
                Lead.is_duplicate.is_(False),
                Company.qualification_status == QualificationStatus.APPROVED,
            )
        )
        if tier:
            stmt = stmt.where(Lead.tier == tier)
        stmt = stmt.order_by(Lead.total_score.desc().nulls_last())
        return list(self.db.scalars(stmt))

    def list_scorable_for_import(self, lead_import_id: int) -> list[Lead]:
        """Leads whose company was approved (or approved-after-review), excluding duplicates."""
        stmt = (
            select(Lead)
            .join(Company, Lead.company_id == Company.id)
            .where(
                Lead.lead_import_id == lead_import_id,
                Lead.is_duplicate.is_(False),
                Company.qualification_status == QualificationStatus.APPROVED,
            )
        )
        return list(self.db.scalars(stmt))
