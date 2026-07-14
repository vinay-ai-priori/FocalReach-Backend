"""Symmetric encryption for secrets that must be readable again later (unlike password
hashes) — currently just mailbox app passwords. Uses Fernet (AES-128-CBC + HMAC)."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import ExternalServiceError


@lru_cache
def _fernet() -> Fernet:
    key = settings.MAILBOX_CREDENTIALS_KEY
    if not key:
        # Dev-only fallback so the app runs without extra setup. Never reached in
        # production if MAILBOX_CREDENTIALS_KEY is set, as it should be.
        key = Fernet.generate_key().decode()
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ExternalServiceError(
            "Stored credential could not be decrypted (encryption key changed or data corrupted)."
        ) from exc
