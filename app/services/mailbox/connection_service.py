"""Verifies a mailbox's IMAP/SMTP credentials by actually logging in — not a stub.
Both protocols are checked because outreach needs SMTP to send and IMAP to (later)
read replies; either failing means the connection isn't usable end-to-end."""

import imaplib
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.services.mailbox.providers import ProviderPreset

logger = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS = 10


@dataclass
class VerificationResult:
    ok: bool
    error: str | None = None


def _friendly_auth_error(provider_display_name: str) -> str:
    return (
        f"{provider_display_name} rejected the email/app password combination. "
        "Double-check the address and that you pasted the app password (not your regular password)."
    )


def verify_mailbox_credentials(preset: ProviderPreset, email_address: str, app_password: str) -> VerificationResult:
    imap_error = _verify_imap(preset, email_address, app_password)
    if imap_error:
        return VerificationResult(ok=False, error=imap_error)

    smtp_error = _verify_smtp(preset, email_address, app_password)
    if smtp_error:
        return VerificationResult(ok=False, error=smtp_error)

    return VerificationResult(ok=True)


def _verify_imap(preset: ProviderPreset, email_address: str, app_password: str) -> str | None:
    try:
        with imaplib.IMAP4_SSL(preset.imap_host, preset.imap_port, timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            conn.login(email_address, app_password)
        return None
    except imaplib.IMAP4.error:
        return _friendly_auth_error(preset.display_name)
    except (OSError, TimeoutError) as exc:
        logger.warning("IMAP connection to %s failed: %s", preset.imap_host, exc)
        return f"Could not reach {preset.imap_host}:{preset.imap_port} — check your network and try again."
    except Exception as exc:
        logger.exception("Unexpected IMAP verification error for %s", email_address)
        return f"IMAP verification failed unexpectedly: {exc}"


def _verify_smtp(preset: ProviderPreset, email_address: str, app_password: str) -> str | None:
    try:
        with smtplib.SMTP(preset.smtp_host, preset.smtp_port, timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(email_address, app_password)
        return None
    except smtplib.SMTPAuthenticationError:
        return _friendly_auth_error(preset.display_name)
    except (OSError, TimeoutError, smtplib.SMTPException) as exc:
        logger.warning("SMTP connection to %s failed: %s", preset.smtp_host, exc)
        return f"Could not reach {preset.smtp_host}:{preset.smtp_port} — check your network and try again."
    except Exception as exc:
        logger.exception("Unexpected SMTP verification error for %s", email_address)
        return f"SMTP verification failed unexpectedly: {exc}"


def send_email_via_smtp(
    preset: ProviderPreset,
    email_address: str,
    app_password: str,
    *,
    to: str,
    subject: str,
    body: str,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> None:
    """Sends a single plaintext email through the user's own mailbox (their app
    password), so outreach comes from the rep's real address rather than a shared one.

    `message_id` (RFC 5322 Message-ID) lets callers stamp the id BEFORE dispatch so an
    interrupted send can later be verified against the mailbox's Sent folder.

    `in_reply_to`/`references` thread every email in a lead's sequence into one
    conversation in the prospect's mail client (and are what the reply poller matches
    inbound replies back against — see app/services/inbox/imap_poll_service.py).

    Raised ExternalServiceError carries `.transient`: True for network/temporary
    failures (safe to auto-retry), False for auth/permanent rejections (retrying a bad
    password just gets the mailbox locked).
    """
    message = EmailMessage()
    message["From"] = email_address
    message["To"] = to
    message["Subject"] = subject
    if message_id:
        message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body)

    try:
        with smtplib.SMTP(preset.smtp_host, preset.smtp_port, timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(email_address, app_password)
            conn.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        error = ExternalServiceError(_friendly_auth_error(preset.display_name))
        error.transient = False
        raise error from exc
    except smtplib.SMTPRecipientsRefused as exc:
        error = ExternalServiceError(f"The recipient address {to} was rejected by the mail server.")
        error.transient = False
        raise error from exc
    except (OSError, TimeoutError, smtplib.SMTPException) as exc:
        logger.warning("SMTP send via %s failed: %s", preset.smtp_host, exc)
        error = ExternalServiceError(
            f"Could not reach {preset.smtp_host}:{preset.smtp_port} — check your network and try again."
        )
        error.transient = True
        raise error from exc
    except Exception as exc:
        logger.exception("Unexpected SMTP send error for %s", email_address)
        error = ExternalServiceError(f"Sending failed unexpectedly: {exc}")
        error.transient = False
        raise error from exc
