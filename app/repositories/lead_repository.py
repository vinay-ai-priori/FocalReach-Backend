from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models.company import CompanyQualification, QualificationStatus
from app.models.lead import Lead, LeadTier
from app.repositories.base import BaseRepository


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    def _approved_for_import(self, lead_import_id: int):
        """Base statement: this run's leads whose company was APPROVED in THIS run's
        qualification (verdicts are per lead_import, companies are canonical)."""
        return (
            select(Lead)
            .join(
                CompanyQualification,
                (CompanyQualification.company_id == Lead.company_id)
                & (CompanyQualification.lead_import_id == Lead.lead_import_id),
            )
            .where(
                Lead.lead_import_id == lead_import_id,
                Lead.is_duplicate.is_(False),
                CompanyQualification.qualification_status.in_(
                    (QualificationStatus.APPROVED, QualificationStatus.REACTIVATED)
                ),
            )
        )

    def list_for_import(self, lead_import_id: int, tier: LeadTier | None = None) -> list[Lead]:
        # The working set for prioritization/outreach is leads of APPROVED companies only
        # (leads of rejected / needs-review companies are never scored or contacted).
        # Deduplicated leads are also excluded (kept in DB for auditing).
        stmt = self._approved_for_import(lead_import_id).options(joinedload(Lead.company))
        if tier:
            stmt = stmt.where(Lead.tier == tier)
        stmt = stmt.order_by(Lead.total_score.desc().nulls_last())
        return list(self.db.scalars(stmt))

    def list_scorable_for_import(self, lead_import_id: int) -> list[Lead]:
        """Leads whose company was approved (or approved-after-review), excluding duplicates."""
        return list(self.db.scalars(self._approved_for_import(lead_import_id)))
