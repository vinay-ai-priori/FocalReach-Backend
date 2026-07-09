from sqlalchemy import select

from app.models.email_draft import EmailDraft
from app.models.lead import Lead
from app.repositories.base import BaseRepository


class EmailDraftRepository(BaseRepository[EmailDraft]):
    model = EmailDraft

    def get_latest_for_lead(self, lead_id: int) -> EmailDraft | None:
        stmt = select(EmailDraft).where(EmailDraft.lead_id == lead_id).order_by(EmailDraft.id.desc())
        return self.db.scalars(stmt).first()

    def list_for_import(self, lead_import_id: int) -> list[EmailDraft]:
        stmt = (
            select(EmailDraft)
            .join(Lead, EmailDraft.lead_id == Lead.id)
            .where(Lead.lead_import_id == lead_import_id)
            .order_by(EmailDraft.id.desc())
        )
        return list(self.db.scalars(stmt))
