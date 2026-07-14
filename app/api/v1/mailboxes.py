from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.models.mailbox_connection import MailboxConnection
from app.models.user import User
from app.repositories.mailbox_repository import MailboxConnectionRepository
from app.schemas.common import Message
from app.schemas.mailbox import MailboxConnectionOut, MailboxConnectRequest, MailboxProviderOut
from app.services.mailbox.connection_service import verify_mailbox_credentials
from app.services.mailbox.providers import PROVIDER_PRESETS, get_preset

logger = get_logger(__name__)

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"], dependencies=[Depends(get_current_user)])


def _get_owned(repo: MailboxConnectionRepository, mailbox_id: UUID, user: User) -> MailboxConnection:
    mailbox = repo.get_by_public_id(mailbox_id)
    if not mailbox or mailbox.user_id != user.id:
        raise NotFoundError(f"Mailbox {mailbox_id} not found.")
    return mailbox


@router.get("/providers", response_model=list[MailboxProviderOut])
def list_providers() -> list[MailboxProviderOut]:
    return [
        MailboxProviderOut(
            provider=preset.provider,
            display_name=preset.display_name,
            app_password_url=preset.app_password_url,
            instructions=preset.instructions,
        )
        for preset in PROVIDER_PRESETS.values()
    ]


@router.get("", response_model=list[MailboxConnectionOut])
def list_my_mailboxes(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MailboxConnectionOut]:
    return [
        MailboxConnectionOut.model_validate(m) for m in MailboxConnectionRepository(db).list_for_user(user.id)
    ]


@router.post("/connect", response_model=MailboxConnectionOut)
def connect_mailbox(
    payload: MailboxConnectRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MailboxConnectionOut:
    """Verifies the credentials by actually logging into IMAP and SMTP before ever
    persisting anything — a failed connection is never saved. Only one mailbox per
    user: delete the existing one first to connect a different address."""
    repo = MailboxConnectionRepository(db)
    existing = repo.list_for_user(user.id)
    if existing:
        raise ConflictError(
            f"You already have a mailbox connected ({existing[0].email_address}). "
            "Delete it first to connect a different one."
        )

    preset = get_preset(payload.provider)
    result = verify_mailbox_credentials(preset, payload.email, payload.app_password)
    if not result.ok:
        raise ValidationFailedError(result.error or "Could not verify mailbox credentials.")

    mailbox = repo.create(
        MailboxConnection(
            user_id=user.id,
            provider=payload.provider,
            email_address=payload.email,
            imap_host=preset.imap_host,
            imap_port=preset.imap_port,
            smtp_host=preset.smtp_host,
            smtp_port=preset.smtp_port,
            encrypted_app_password=encrypt_secret(payload.app_password),
            is_connected=True,
        )
    )
    return MailboxConnectionOut.model_validate(mailbox)


@router.post("/{mailbox_id}/disconnect", response_model=MailboxConnectionOut)
def disconnect_mailbox(
    mailbox_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MailboxConnectionOut:
    """Temporary: flips the mailbox off without touching the stored credentials or
    deleting the record — reconnect to turn it back on."""
    repo = MailboxConnectionRepository(db)
    mailbox = _get_owned(repo, mailbox_id, user)
    mailbox = repo.update(mailbox, is_connected=False)
    return MailboxConnectionOut.model_validate(mailbox)


@router.post("/{mailbox_id}/reconnect", response_model=MailboxConnectionOut)
def reconnect_mailbox(
    mailbox_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MailboxConnectionOut:
    """Re-verifies the stored app password against IMAP/SMTP and reactivates the
    mailbox only if it's still valid — a revoked app password stays disconnected
    with the failure reason recorded."""
    repo = MailboxConnectionRepository(db)
    mailbox = _get_owned(repo, mailbox_id, user)
    preset = get_preset(mailbox.provider)
    app_password = decrypt_secret(mailbox.encrypted_app_password)
    result = verify_mailbox_credentials(preset, mailbox.email_address, app_password)
    if not result.ok:
        mailbox = repo.update(mailbox, is_connected=False, last_verification_error=result.error)
        raise ValidationFailedError(result.error or "Could not verify mailbox credentials.")
    mailbox = repo.update(mailbox, is_connected=True, last_verification_error=None)
    return MailboxConnectionOut.model_validate(mailbox)


@router.delete("/{mailbox_id}", response_model=Message)
def delete_mailbox(
    mailbox_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Message:
    """Permanently removes the connection (and its stored credentials) — the only way
    to free up connecting a different mailbox, since only one is allowed per user."""
    repo = MailboxConnectionRepository(db)
    mailbox = _get_owned(repo, mailbox_id, user)
    email = mailbox.email_address
    repo.delete(mailbox)
    return Message(message=f"Deleted '{email}'.")
