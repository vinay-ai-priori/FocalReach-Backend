"""Answers ONE question for an interrupted dispatch: is the email with this
Message-ID in the mailbox's Sent folder?

Three-valued on purpose — the caller's safety depends on it:
- FOUND      -> the send definitely completed (provider filed the Sent copy).
- NOT_FOUND  -> every Sent folder was searched successfully and the copy is absent:
                the send definitely did NOT complete. Only this value may trigger an
                automatic retry, because only it PROVES the prospect got nothing.
- UNKNOWN    -> anything less than proof (IMAP unreachable, login failed, no folder
                selectable, search errored). The caller must fall back to a human.

This function must never raise: an exception during verification is just UNKNOWN.
"""

import imaplib
from enum import Enum

from app.core.crypto import decrypt_secret
from app.core.logging import get_logger
from app.models.mailbox_connection import MailboxConnection
from app.services.mailbox.providers import get_preset

logger = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS = 15


class SentVerification(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


def verify_message_in_sent_folder(mailbox: MailboxConnection, message_id: str | None) -> SentVerification:
    if not message_id:
        return SentVerification.UNKNOWN  # nothing to search for — can't prove anything

    # HEADER search matches substrings of the raw header, so search without the
    # angle brackets — matches whether or not the server indexes them.
    needle = message_id.strip().strip("<>")
    if not needle:
        return SentVerification.UNKNOWN

    preset = get_preset(mailbox.provider)
    try:
        app_password = decrypt_secret(mailbox.encrypted_app_password)
        with imaplib.IMAP4_SSL(preset.imap_host, preset.imap_port, timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            conn.login(mailbox.email_address, app_password)

            searched_any_folder = False
            for folder in preset.sent_folders:
                try:
                    status, _ = conn.select(f'"{folder}"', readonly=True)
                except Exception:
                    continue
                if status != "OK":
                    continue
                status, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", f'"{needle}"')
                if status != "OK":
                    # This folder couldn't be searched — its contents stay unproven.
                    continue
                searched_any_folder = True
                if data and data[0] and data[0].split():
                    return SentVerification.FOUND

            # NOT_FOUND requires proof of absence: at least one Sent folder was
            # actually searched successfully and came back empty.
            return SentVerification.NOT_FOUND if searched_any_folder else SentVerification.UNKNOWN
    except Exception as exc:
        logger.warning(
            "Sent-folder verification unavailable for %s (%s): %s",
            mailbox.email_address, mailbox.provider, exc,
        )
        return SentVerification.UNKNOWN
