from sqlalchemy import select

from app.models.mailbox_connection import MailboxConnection
from app.repositories.base import BaseRepository


class MailboxConnectionRepository(BaseRepository[MailboxConnection]):
    model = MailboxConnection

    def list_for_user(self, user_id: int) -> list[MailboxConnection]:
        stmt = select(MailboxConnection).where(MailboxConnection.user_id == user_id).order_by(
            MailboxConnection.created_at.desc()
        )
        return list(self.db.scalars(stmt))

    def get_by_user_and_email(self, user_id: int, email_address: str) -> MailboxConnection | None:
        stmt = select(MailboxConnection).where(
            MailboxConnection.user_id == user_id, MailboxConnection.email_address == email_address
        )
        return self.db.scalars(stmt).first()
