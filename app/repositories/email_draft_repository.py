from sqlalchemy import select

from app.models.email_draft import STEP_INITIAL, DraftChannel, EmailDraft
from app.models.lead import Lead
from app.repositories.base import BaseRepository


class EmailDraftRepository(BaseRepository[EmailDraft]):
    model = EmailDraft

    def get_latest_for_lead(self, lead_id: int) -> EmailDraft | None:
        """Latest INITIAL email draft (step 1) — the pre-sequence behavior every
        existing caller (batch drafting, the lead's draft endpoint) expects."""
        stmt = (
            select(EmailDraft)
            .where(
                EmailDraft.lead_id == lead_id,
                EmailDraft.channel == DraftChannel.EMAIL,
                EmailDraft.step_index == STEP_INITIAL,
            )
            .order_by(EmailDraft.id.desc())
        )
        return self.db.scalars(stmt).first()

    def get_step(self, lead_id: int, channel: DraftChannel, step_index: int) -> EmailDraft | None:
        stmt = (
            select(EmailDraft)
            .where(
                EmailDraft.lead_id == lead_id,
                EmailDraft.channel == channel,
                EmailDraft.step_index == step_index,
            )
            .order_by(EmailDraft.id.desc())
        )
        return self.db.scalars(stmt).first()

    def list_for_lead(self, lead_id: int) -> list[EmailDraft]:
        """The lead's whole sequence, newest row per step first within step order."""
        stmt = (
            select(EmailDraft)
            .where(EmailDraft.lead_id == lead_id)
            .order_by(EmailDraft.step_index, EmailDraft.id.desc())
        )
        return list(self.db.scalars(stmt))

    def list_for_import(self, lead_import_id: int) -> list[EmailDraft]:
        """Initial drafts only — feeds the workspace lead list, one row per lead.
        Follow-up/LinkedIn/call steps come from list_for_lead per selected lead."""
        stmt = (
            select(EmailDraft)
            .join(Lead, EmailDraft.lead_id == Lead.id)
            .where(
                Lead.lead_import_id == lead_import_id,
                EmailDraft.channel == DraftChannel.EMAIL,
                EmailDraft.step_index == STEP_INITIAL,
            )
            .order_by(EmailDraft.id.desc())
        )
        return list(self.db.scalars(stmt))
