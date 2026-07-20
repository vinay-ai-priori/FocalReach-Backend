"""Ownership checks: campaigns are private to their creating user, for every role.

v2 schema: ownership is derived through the single path import → campaign → user."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.api.auth_deps import Forbidden
from app.core.exceptions import NotFoundError
from app.models.lead_import import LeadImport
from app.models.user import User
from app.repositories.lead_import_repository import LeadImportRepository


def get_owned_import(db: Session, import_public_id: UUID, user: User) -> LeadImport:
    lead_import = LeadImportRepository(db).get_by_public_id(import_public_id)
    if not lead_import:
        raise NotFoundError(f"Import {import_public_id} not found.")
    assert_import_owned(lead_import, user)
    return lead_import


def assert_import_owned(lead_import: LeadImport | None, user: User) -> None:
    if lead_import and lead_import.campaign.user_id != user.id:
        raise Forbidden("This campaign belongs to another user.")
