"""Operational CLI. Usage:  python -m app.cli seed-superadmin"""

import sys

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository

configure_logging()
logger = get_logger(__name__)


def seed_superadmin() -> None:
    """Idempotently creates the app-owner account from SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD.
    This is the ONLY way a super admin can ever be created."""
    email = settings.SUPERADMIN_EMAIL.strip().lower()
    password = settings.SUPERADMIN_PASSWORD
    if not email or not password:
        print("Set SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD in backend/.env first.")
        sys.exit(1)

    db = SessionLocal()
    try:
        repo = UserRepository(db)
        existing = repo.get_by_email(email)
        if existing:
            if existing.role != UserRole.SUPER_ADMIN:
                print(f"ERROR: {email} exists but is not a super admin. Choose a different email.")
                sys.exit(1)
            print(f"Super admin {email} already exists (id {existing.id}). Nothing to do.")
            return
        user = repo.create(
            User(
                email=email,
                full_name=settings.SUPERADMIN_NAME or "App Owner",
                hashed_password=hash_password(password),
                role=UserRole.SUPER_ADMIN,
                organization_id=None,
                must_change_password=False,
            )
        )
        print(f"Super admin created: {email} (id {user.id})")
    finally:
        db.close()


COMMANDS = {"seed-superadmin": seed_superadmin}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m app.cli [{'|'.join(COMMANDS)}]")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
