from sqlalchemy import select

from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email.strip().lower()))

    def list_all(self, organization_id: int | None = None) -> list[User]:
        stmt = select(User).order_by(User.created_at.desc())
        if organization_id is not None:
            stmt = stmt.where(User.organization_id == organization_id)
        return list(self.db.scalars(stmt))


class TenantRepository(BaseRepository[Tenant]):
    model = Tenant

    def get_by_name(self, name: str) -> Tenant | None:
        return self.db.scalar(select(Tenant).where(Tenant.name == name))


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization

    def list_for_tenant(self, tenant_id: int) -> list[Organization]:
        return list(self.db.scalars(select(Organization).where(Organization.tenant_id == tenant_id)))


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    def revoke_all_for_user(self, user_id: int) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for token in self.db.scalars(
            select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        ):
            token.revoked_at = now
        self.db.commit()
