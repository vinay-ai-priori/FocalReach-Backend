from sqlalchemy import select

from app.models.calcom_connection import CalComConnection
from app.repositories.base import BaseRepository


class CalComConnectionRepository(BaseRepository[CalComConnection]):
    model = CalComConnection

    def get_for_user(self, user_id: int) -> CalComConnection | None:
        stmt = select(CalComConnection).where(CalComConnection.user_id == user_id)
        return self.db.scalars(stmt).first()

    def get_for_user_locked(self, user_id: int) -> CalComConnection | None:
        """Row-locked read used before a token refresh so concurrent requests for the
        same user can't both refresh (and race-invalidate) the same refresh token."""
        stmt = select(CalComConnection).where(CalComConnection.user_id == user_id).with_for_update()
        return self.db.scalars(stmt).first()
