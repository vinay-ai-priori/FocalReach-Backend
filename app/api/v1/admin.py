"""Administration panel API — every route is super-admin-only via the router-level guard."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth_deps import require_super_admin
from app.api.deps import get_db
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.core.security import hash_password, validate_password_strength
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.repositories.user_repository import (
    OrganizationRepository,
    RefreshTokenRepository,
    TenantRepository,
    UserRepository,
)
from app.schemas.common import Message
from app.schemas.admin import (
    AdminUserOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    SessionOut,
    TenantCreate,
    TenantOut,
    TenantUpdate,
    UserCreate,
    UserUpdate,
)


def _parse_device(user_agent: str | None) -> str:
    """Best-effort browser label from a User-Agent string (order matters — Edge/Chrome
    both contain 'Chrome', so check the more specific brands first)."""
    if not user_agent:
        return "Unknown"
    ua = user_agent.lower()
    for needle, label in (
        ("edg", "Edge"),
        ("opr", "Opera"),
        ("firefox", "Firefox"),
        ("chrome", "Chrome"),
        ("safari", "Safari"),
    ):
        if needle in ua:
            return label
    return "Unknown"

router = APIRouter(prefix="/admin", tags=["administration"], dependencies=[Depends(require_super_admin)])


# ---------------- Tenants ----------------
def _tenant_out(db: Session, tenant: Tenant) -> TenantOut:
    count = db.scalar(select(func.count(Organization.id)).where(Organization.tenant_id == tenant.id)) or 0
    out = TenantOut.model_validate(tenant)
    out.organization_count = count
    return out


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db)) -> list[TenantOut]:
    return [_tenant_out(db, t) for t in TenantRepository(db).list()]


@router.post("/tenants", response_model=TenantOut)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)) -> TenantOut:
    repo = TenantRepository(db)
    if repo.get_by_name(payload.name.strip()):
        raise ConflictError(f"A tenant named '{payload.name}' already exists.")
    tenant = repo.create(Tenant(name=payload.name.strip(), criteria=payload.criteria))
    return _tenant_out(db, tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
def update_tenant(tenant_id: UUID, payload: TenantUpdate, db: Session = Depends(get_db)) -> TenantOut:
    repo = TenantRepository(db)
    tenant = repo.get_by_public_id(tenant_id)
    if not tenant:
        raise NotFoundError(f"Tenant {tenant_id} not found.")
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if fields:
        tenant = repo.update(tenant, **fields)
    return _tenant_out(db, tenant)


@router.delete("/tenants/{tenant_id}", response_model=Message)
def delete_tenant(tenant_id: UUID, db: Session = Depends(get_db)) -> Message:
    """Hard delete. Cascades to the tenant's organizations; users in those orgs are detached
    (organization set to NULL), not deleted."""
    repo = TenantRepository(db)
    tenant = repo.get_by_public_id(tenant_id)
    if not tenant:
        raise NotFoundError(f"Tenant {tenant_id} not found.")
    repo.delete(tenant)
    return Message(message=f"Tenant '{tenant.name}' deleted.")


# ---------------- Organizations ----------------
def _org_out(db: Session, org: Organization) -> OrganizationOut:
    count = db.scalar(select(func.count(User.id)).where(User.organization_id == org.id)) or 0
    out = OrganizationOut.model_validate(org)
    out.user_count = count
    out.tenant_name = org.tenant.name if org.tenant else None
    out.tenant_public_id = org.tenant.public_id if org.tenant else None
    return out


@router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(db: Session = Depends(get_db)) -> list[OrganizationOut]:
    return [_org_out(db, o) for o in OrganizationRepository(db).list()]


@router.post("/organizations", response_model=OrganizationOut)
def create_organization(payload: OrganizationCreate, db: Session = Depends(get_db)) -> OrganizationOut:
    tenant = TenantRepository(db).get_by_public_id(payload.tenant_id)
    if not tenant:
        raise NotFoundError(f"Tenant {payload.tenant_id} not found.")
    existing = db.scalar(
        select(Organization).where(
            Organization.tenant_id == tenant.id, Organization.name == payload.name.strip()
        )
    )
    if existing:
        raise ConflictError(f"Organization '{payload.name}' already exists in this tenant.")
    org = OrganizationRepository(db).create(Organization(tenant_id=tenant.id, name=payload.name.strip()))
    return _org_out(db, org)


@router.patch("/organizations/{org_id}", response_model=OrganizationOut)
def update_organization(org_id: UUID, payload: OrganizationUpdate, db: Session = Depends(get_db)) -> OrganizationOut:
    repo = OrganizationRepository(db)
    org = repo.get_by_public_id(org_id)
    if not org:
        raise NotFoundError(f"Organization {org_id} not found.")
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "tenant_id" in fields:
        tenant = TenantRepository(db).get_by_public_id(fields["tenant_id"])
        if not tenant:
            raise NotFoundError(f"Tenant {fields['tenant_id']} not found.")
        fields["tenant_id"] = tenant.id
    if fields:
        org = repo.update(org, **fields)
    return _org_out(db, org)


@router.delete("/organizations/{org_id}", response_model=Message)
def delete_organization(org_id: UUID, db: Session = Depends(get_db)) -> Message:
    """Hard delete. Users of this organization are detached (organization set to NULL)."""
    repo = OrganizationRepository(db)
    org = repo.get_by_public_id(org_id)
    if not org:
        raise NotFoundError(f"Organization {org_id} not found.")
    repo.delete(org)
    return Message(message=f"Organization '{org.name}' deleted.")


# ---------------- Users ----------------
def _user_out(user: User) -> AdminUserOut:
    out = AdminUserOut.model_validate(user)
    out.organization_name = user.organization.name if user.organization else None
    out.organization_public_id = user.organization.public_id if user.organization else None
    return out


@router.get("/users", response_model=list[AdminUserOut])
def list_users(organization_id: UUID | None = None, db: Session = Depends(get_db)) -> list[AdminUserOut]:
    org = OrganizationRepository(db).get_by_public_id(organization_id) if organization_id else None
    return [_user_out(u) for u in UserRepository(db).list_all(org.id if org else None)]


@router.post("/users", response_model=AdminUserOut)
def create_user(payload: UserCreate, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)) -> AdminUserOut:
    repo = UserRepository(db)
    email = payload.email.strip().lower()
    if repo.get_by_email(email):
        raise ConflictError(f"An account with email '{email}' already exists.")
    org = OrganizationRepository(db).get_by_public_id(payload.organization_id)
    if not org:
        raise NotFoundError(f"Organization {payload.organization_id} not found.")
    error = validate_password_strength(payload.password)
    if error:
        raise ValidationFailedError(error)
    user = repo.create(
        User(
            full_name=payload.full_name.strip(),
            email=email,
            hashed_password=hash_password(payload.password),
            organization_id=org.id,
            role=UserRole.USER,  # only the CLI seed can create a super admin
            must_change_password=True,
            created_by_id=admin.id,
        )
    )
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(user_id: UUID, payload: UserUpdate, db: Session = Depends(get_db)) -> AdminUserOut:
    repo = UserRepository(db)
    user = repo.get_by_public_id(user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found.")
    if user.role == UserRole.SUPER_ADMIN:
        raise ValidationFailedError("The super admin account cannot be modified from the admin panel.")

    fields = payload.model_dump(exclude_unset=True)
    new_password = fields.pop("new_password", None)
    fields = {k: v for k, v in fields.items() if v is not None}
    if new_password:
        error = validate_password_strength(new_password)
        if error:
            raise ValidationFailedError(error)
        fields["hashed_password"] = hash_password(new_password)
        fields["must_change_password"] = True
    if "organization_id" in fields:
        org = OrganizationRepository(db).get_by_public_id(fields["organization_id"])
        if not org:
            raise NotFoundError(f"Organization {fields['organization_id']} not found.")
        fields["organization_id"] = org.id

    if fields:
        user = repo.update(user, **fields)
    if new_password or fields.get("is_active") is False:
        RefreshTokenRepository(db).revoke_all_for_user(user.id)  # kill live sessions
    return _user_out(user)


@router.delete("/users/{user_id}", response_model=Message)
def delete_user(user_id: UUID, db: Session = Depends(get_db)) -> Message:
    """Hard delete. The user's campaigns remain in the database (detached from the account)
    and still count toward organization-level deduplication."""
    repo = UserRepository(db)
    user = repo.get_by_public_id(user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found.")
    if user.role == UserRole.SUPER_ADMIN:
        raise ValidationFailedError("The super admin account cannot be deleted from the admin panel.")
    repo.delete(user)
    return Message(message=f"User '{user.email}' deleted.")


# ---------------- Sessions ----------------
@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionOut]:
    """Active logins, one row per user. A session is live while its refresh token is
    neither revoked nor expired; rotation keeps exactly one live token per session."""
    now = datetime.now(timezone.utc)
    live = db.scalars(
        select(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now)
        .order_by(RefreshToken.created_at.desc())
    ).all()

    by_user: dict[int, dict] = {}
    for tok in live:
        entry = by_user.get(tok.user_id)
        if entry is None:
            # First (most recent, since ordered desc) token for this user drives the row.
            by_user[tok.user_id] = {"latest": tok, "count": 1}
        else:
            entry["count"] += 1

    sessions: list[SessionOut] = []
    for user_id, entry in by_user.items():
        user = UserRepository(db).get(user_id)
        if not user or not user.is_active:
            continue
        latest: RefreshToken = entry["latest"]
        sessions.append(
            SessionOut(
                user_public_id=user.public_id,
                full_name=user.full_name,
                email=user.email,
                role=user.role.value,
                organization_name=user.organization.name if user.organization else None,
                signed_in_at=user.last_login_at,
                last_active_at=latest.created_at,
                device=_parse_device(latest.user_agent),
                session_count=entry["count"],
            )
        )
    sessions.sort(key=lambda s: s.last_active_at, reverse=True)
    return sessions


@router.delete("/sessions/{user_id}", response_model=Message)
def terminate_session(user_id: UUID, db: Session = Depends(get_db)) -> Message:
    """Revoke every live refresh token for the user — signs them out immediately."""
    user = UserRepository(db).get_by_public_id(user_id)
    if not user:
        raise NotFoundError(f"User {user_id} not found.")
    RefreshTokenRepository(db).revoke_all_for_user(user.id)
    return Message(message=f"Session for '{user.email}' terminated.")
