"""Reads new inbox messages via IMAP and matches them back to a lead's outreach
thread. Runs from app/tasks/inbox_poll_tasks.py every 10 minutes, one mailbox at a
time. Nothing here calls the LLM or acts on a reply — see reply_router.py for that;
this module's only job is "find new mail, dedupe it, tie it to a lead if possible."
"""

import email
import email.policy
import imaplib
import re
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.email_draft import EmailDraft
from app.models.inbound_reply import InboundReply
from app.models.lead import Lead
from app.models.lead_import import LeadImport
from app.models.mailbox_connection import MailboxConnection
from app.services.mailbox.providers import get_preset

logger = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS = 20

# Auto-replies/bounces/list mail should never be treated as a prospect's reply.
_AUTO_SENDER_PATTERNS = re.compile(
    r"(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounce|notifications?@)", re.IGNORECASE
)
_QUOTE_MARKERS = re.compile(
    r"^(On .{0,120} wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+)$", re.IGNORECASE | re.MULTILINE
)


def _is_auto_reply(msg: "email.message.EmailMessage", from_address: str) -> bool:
    auto_submitted = (msg.get("Auto-Submitted") or "no").strip().lower()
    if auto_submitted not in ("", "no"):
        return True
    precedence = (msg.get("Precedence") or "").strip().lower()
    if precedence in ("bulk", "junk", "list", "auto_reply"):
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    if _AUTO_SENDER_PATTERNS.search(from_address or ""):
        return True
    return False


def _extract_plain_text(msg: "email.message.EmailMessage") -> str:
    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        return ""
    try:
        text = body_part.get_content()
    except Exception:
        return ""
    if body_part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    # Cut at the first quoted-reply marker so classification/extraction only sees the
    # prospect's new text, not the whole quoted thread history.
    match = _QUOTE_MARKERS.search(text)
    if match:
        text = text[: match.start()]
    lines = [line.rstrip() for line in text.splitlines() if not line.strip().startswith(">")]
    return "\n".join(lines).strip()[:8000]


def _match_lead(db: Session, user_id: int, references: list[str], from_address: str) -> tuple[Lead | None, EmailDraft | None]:
    if references:
        stmt = (
            select(EmailDraft, Lead)
            .join(Lead, EmailDraft.lead_id == Lead.id)
            .join(LeadImport, Lead.lead_import_id == LeadImport.id)
            .join(Campaign, LeadImport.campaign_id == Campaign.id)
            .where(Campaign.user_id == user_id, EmailDraft.message_id.in_(references))
        )
        row = db.execute(stmt).first()
        if row:
            draft, lead = row
            return lead, draft

    if from_address:
        stmt = (
            select(Lead)
            .join(LeadImport, Lead.lead_import_id == LeadImport.id)
            .join(Campaign, LeadImport.campaign_id == Campaign.id)
            .where(Campaign.user_id == user_id, Lead.email == from_address)
        )
        lead = db.scalars(stmt).first()
        if lead:
            last_draft = db.scalars(
                select(EmailDraft)
                .where(EmailDraft.lead_id == lead.id, EmailDraft.message_id.is_not(None))
                .order_by(EmailDraft.sent_at.desc().nullslast())
            ).first()
            return lead, last_draft

    return None, None


def poll_mailbox(db: Session, mailbox: MailboxConnection) -> list[InboundReply]:
    """Fetches new messages since the mailbox's last cursor, dedupes, and matches each
    to a lead where possible. Returns newly-created rows that have a matched lead_id
    (ready for classification) — unmatched/duplicate rows are persisted but not
    returned, since there's nothing further to do with them automatically."""
    preset = get_preset(mailbox.provider)
    app_password = decrypt_secret(mailbox.encrypted_app_password)
    new_rows: list[InboundReply] = []

    with imaplib.IMAP4_SSL(preset.imap_host, preset.imap_port, timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        conn.login(mailbox.email_address, app_password)
        status, data = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not select INBOX for {mailbox.email_address}: {data}")

        uidvalidity = _fetch_uidvalidity(conn)
        first_poll = mailbox.last_polled_uid is None
        resynced = mailbox.imap_uidvalidity is not None and uidvalidity != mailbox.imap_uidvalidity
        if resynced:
            logger.warning("UIDVALIDITY changed for mailbox %s — resetting poll cursor", mailbox.email_address)

        if first_poll or resynced:
            # Bootstrap: don't backfill years of old mail as "new replies" — just
            # establish the cursor at the current tip and start processing from the
            # next poll onward.
            highest = _fetch_highest_uid(conn)
            mailbox.imap_uidvalidity = uidvalidity
            mailbox.last_polled_uid = highest
            mailbox.last_polled_at = datetime.now(timezone.utc)
            db.commit()
            return []

        start_uid = mailbox.last_polled_uid + 1
        status, data = conn.uid("search", None, f"UID {start_uid}:*")
        if status != "OK":
            raise RuntimeError(f"IMAP UID SEARCH failed for {mailbox.email_address}: {data}")
        uids = [int(u) for u in data[0].split()] if data and data[0] else []
        # A range search with nothing above start_uid still returns start_uid-1's
        # neighbour in some servers; guard against re-processing the boundary.
        uids = sorted(u for u in uids if u >= start_uid)[: settings.INBOX_POLL_BATCH_SIZE]

        max_uid_seen = mailbox.last_polled_uid
        for uid in uids:
            max_uid_seen = max(max_uid_seen, uid)
            try:
                row = _fetch_and_store_one(db, conn, mailbox, uid)
            except Exception:
                logger.exception("Failed to fetch/parse UID %s for mailbox %s", uid, mailbox.email_address)
                continue
            if row is not None and row.lead_id is not None:
                new_rows.append(row)

        mailbox.last_polled_uid = max_uid_seen
        mailbox.last_polled_at = datetime.now(timezone.utc)
        db.commit()

    return new_rows


def _fetch_uidvalidity(conn: imaplib.IMAP4_SSL) -> int | None:
    status, data = conn.status("INBOX", "(UIDVALIDITY)")
    if status != "OK" or not data or not data[0]:
        return None
    match = re.search(rb"UIDVALIDITY (\d+)", data[0])
    return int(match.group(1)) if match else None


def _fetch_highest_uid(conn: imaplib.IMAP4_SSL) -> int:
    status, data = conn.uid("search", None, "ALL")
    if status != "OK" or not data or not data[0]:
        return 0
    uids = [int(u) for u in data[0].split()]
    return max(uids) if uids else 0


def _fetch_and_store_one(
    db: Session, conn: imaplib.IMAP4_SSL, mailbox: MailboxConnection, uid: int
) -> InboundReply | None:
    status, data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return None
    raw = data[0][1]
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        # Can't dedupe or thread-match a message with no Message-ID — skip it.
        return None

    from_name, from_address = parseaddr(msg.get("From", ""))
    from_address = from_address.lower().strip()

    # Never treat mail the mailbox owner sent to themselves (e.g. via a shared alias)
    # or an obvious auto-reply/bounce as a prospect reply.
    if from_address == mailbox.email_address.lower() or _is_auto_reply(msg, from_address):
        return None

    references_raw = (msg.get("References") or "") + " " + (msg.get("In-Reply-To") or "")
    references = [r.strip() for r in references_raw.split() if r.strip()]

    try:
        received_at = parsedate_to_datetime(msg.get("Date", ""))
        if received_at and received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
    except Exception:
        received_at = None

    lead, matched_draft = _match_lead(db, mailbox.user_id, references, from_address)

    row = InboundReply(
        mailbox_connection_id=mailbox.id,
        lead_id=lead.id if lead else None,
        matched_draft_id=matched_draft.id if matched_draft else None,
        imap_uid=uid,
        imap_message_id=message_id,
        in_reply_to=(msg.get("In-Reply-To") or None),
        from_address=from_address or None,
        subject=(msg.get("Subject") or None),
        body_text=_extract_plain_text(msg),
        received_at=received_at or datetime.now(timezone.utc),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Already seen this message-id for this mailbox — dedupe, not an error.
        db.rollback()
        return None
    db.refresh(row)
    return row
