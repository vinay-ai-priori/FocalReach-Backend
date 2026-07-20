from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.models.crm_connection import CRMConnection, CRMProvider
from app.models.user import User
from app.repositories.crm_repository import CRMConnectionRepository
from app.schemas.crm import CRMConnectRequest, CRMProviderOut
from app.services.crm.adapters import ADAPTERS, get_adapter

router = APIRouter(prefix="/crm", tags=["crm"])


def _org_scope(user: User) -> int | None:
    """NULL organization = the super admin's own scope — full feature access, isolated
    from every real organization."""
    return user.organization_id


@router.get("/providers", response_model=list[CRMProviderOut])
def list_providers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CRMProviderOut]:
    organization_id = _org_scope(user)
    repo = CRMConnectionRepository(db)
    out = []
    for provider, adapter in ADAPTERS.items():
        connection = repo.get_by_provider(organization_id, provider)
        out.append(
            CRMProviderOut(
                provider=provider,
                display_name=adapter.display_name,
                is_connected=bool(connection and connection.is_connected),
                capabilities=adapter.capabilities,
            )
        )
    return out


@router.post("/providers/{provider}/connect", response_model=CRMProviderOut)
def connect(
    provider: CRMProvider,
    payload: CRMConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CRMProviderOut:
    organization_id = _org_scope(user)
    adapter = get_adapter(provider)
    repo = CRMConnectionRepository(db)
    connection = repo.get_by_provider(organization_id, provider)
    is_ok = adapter.test_connection(payload.config)
    if connection:
        repo.update(connection, is_connected=is_ok, config=payload.config)
    else:
        repo.create(
            CRMConnection(
                organization_id=organization_id,
                provider=provider,
                display_name=adapter.display_name,
                is_connected=is_ok,
                config=payload.config,
            )
        )
    return CRMProviderOut(
        provider=provider, display_name=adapter.display_name, is_connected=is_ok, capabilities=adapter.capabilities
    )
