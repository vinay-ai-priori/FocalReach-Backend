"""Dispatch scheduling engine for outreach emails.

Business rules (hardcoded by design):
- Business days: Monday-Friday, in the LEAD's timezone.
- Business hours: 09:00-12:00 and 13:00-16:00 (lead's timezone).
- Scheduled emails keep a 2-minute gap from every other dispatch for the same user.
- Manual "Send" bypasses business hours entirely and only keeps a 30-second gap,
  so the user never waits more than ~30s for a manual send.
- A "schedule now" click dispatches at now + 1 minute (never truly instant — gives
  a cancel window and keeps a single code path).

Concurrency model:
- All slot allocation for one user is serialized with a Postgres transaction-scoped
  advisory lock (pg_advisory_xact_lock), so concurrent Schedule/Send clicks cannot
  pick clashing slots.
- A partial unique index on (scheduled_by_user_id, scheduled_at) is the DB-level
  belt-and-braces: even code that bypasses the lock cannot double-book a slot.
- All "now" values come from the database clock (func.now()), never the app server,
  so web workers and Celery workers can never disagree about time.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationFailedError
from app.models.email_draft import DraftStatus, EmailDraft
from app.models.lead import Lead

BUSINESS_DAYS = (0, 1, 2, 3, 4)  # Monday-Friday (datetime.weekday())
BUSINESS_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(9, 0), time(12, 0)),
    (time(13, 0), time(16, 0)),
)
SCHEDULE_GAP = timedelta(minutes=2)  # gap enforced around scheduled dispatches
SEND_GAP = timedelta(seconds=30)  # gap enforced around manual "Send now" dispatches
IMMEDIATE_DELAY = timedelta(minutes=1)  # "schedule now" actually fires after 1 minute
MAX_WALK_ITERATIONS = 5000  # safety valve; ~7 days of fully-booked 2-min slots

# Statuses that occupy a dispatch slot (their scheduled_at blocks neighbours).
_SLOT_HOLDING_STATUSES = (DraftStatus.SCHEDULED, DraftStatus.SENDING)


def db_now(db: Session) -> datetime:
    """Current time from the DATABASE clock (tz-aware UTC). Single source of truth."""
    now = db.scalar(select(func.now()))
    return now.astimezone(timezone.utc)


def resolve_lead_timezone(db: Session, lead: Lead) -> ZoneInfo:
    """Lead timezone, derived lazily at click time: cached value -> country lookup
    (cached back onto the lead) -> UTC fallback. Never raises for missing data; a bad
    cached string falls through to re-derivation rather than erroring."""
    if lead.timezone:
        try:
            return ZoneInfo(lead.timezone)
        except Exception:
            lead.timezone = None  # stale/invalid cache — re-derive below

    if lead.country:
        from app.services.lead_timezone_service import resolve_timezone_for_country

        try:
            result = resolve_timezone_for_country(lead.country)
        except Exception:
            result = None
        if result and result.timezone:
            try:
                tz = ZoneInfo(result.timezone)
                # Cache on the lead; persisted by the caller's commit (no commit here —
                # callers hold row/advisory locks that a mid-flow commit would release).
                lead.timezone = result.timezone
                return tz
            except Exception:
                pass

    return ZoneInfo("UTC")


def snap_to_business_hours(instant: datetime, tz: ZoneInfo) -> datetime:
    """Earliest instant >= `instant` that falls inside Mon-Fri business windows in `tz`.

    Returns tz-aware UTC. A slot is valid when window_start <= local < window_end,
    so 12:00 snaps to 13:00 and 16:00 rolls to the next business day's 09:00.
    DST gaps/folds are handled by zoneinfo when localizing the window start.
    """
    if instant.tzinfo is None:
        raise ValueError("snap_to_business_hours requires a tz-aware datetime")
    local = instant.astimezone(tz)

    for _ in range(10):  # at most a weekend + a holiday-free week of hops
        if local.weekday() in BUSINESS_DAYS:
            for start, end in BUSINESS_WINDOWS:
                if start <= local.time() < end:
                    return local.astimezone(timezone.utc)
                if local.time() < start:
                    # Before this window opens today — snap to its start.
                    snapped = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
                    # DST gap safety: if the wall time doesn't exist, zoneinfo maps it
                    # into the gap; normalize by round-tripping through UTC.
                    snapped = snapped.astimezone(timezone.utc).astimezone(tz)
                    if snapped < local:  # can happen across a DST fold — never go backwards
                        snapped = local
                    return snapped.astimezone(timezone.utc)
        # Past today's last window (or weekend) — advance to next day 00:00 local.
        local = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    raise RuntimeError("snap_to_business_hours failed to converge")  # pragma: no cover


def find_free_slot(
    candidate: datetime,
    occupied: list[datetime],
    gap: timedelta,
    snap=None,
) -> datetime:
    """Walk `candidate` forward until it is at least `gap` away from every occupied
    instant, re-applying `snap` (e.g. business-hours snapping) after each bump.

    Pure function — the whole collision story is testable without a database.
    `occupied` may contain past instants (recent sends); anything earlier than
    candidate - gap can never conflict and is ignored.
    """
    if snap:
        candidate = snap(candidate)
    slots = sorted(occupied)

    for _ in range(MAX_WALK_ITERATIONS):
        conflict = None
        for slot in slots:
            if candidate - gap < slot < candidate + gap:
                conflict = slot  # keep scanning: later conflicts push further forward
        if conflict is None:
            return candidate
        candidate = conflict + gap
        if snap:
            candidate = snap(candidate)

    raise ValidationFailedError(
        "Could not find a free sending slot in a reasonable horizon — too many emails are already scheduled."
    )


def acquire_user_schedule_lock(db: Session, user_id: int) -> None:
    """Transaction-scoped advisory lock serializing ALL slot allocation for one user.
    Released automatically at commit/rollback. Key space 'outreach-scheduling' is
    namespaced with a constant class id so it can't collide with other advisory locks."""
    db.execute(text("SELECT pg_advisory_xact_lock(742001, :uid)"), {"uid": user_id})


def occupied_slots(
    db: Session,
    user_id: int,
    horizon_start: datetime,
    exclude_draft_id: int | None = None,
) -> list[datetime]:
    """Every instant that currently blocks a neighbouring dispatch for this user:
    scheduled/sending drafts' slots plus real sends since `horizon_start`.
    Call while holding the user's advisory lock or the answer may be stale."""
    from app.models.lead import Lead as LeadModel
    from app.models.lead_import import LeadImport

    stmt = (
        select(EmailDraft.scheduled_at, EmailDraft.sent_at, EmailDraft.status)
        .join(LeadModel, EmailDraft.lead_id == LeadModel.id)
        .join(LeadImport, LeadModel.lead_import_id == LeadImport.id)
        .where(
            LeadImport.user_id == user_id,
            or_(
                EmailDraft.status.in_(_SLOT_HOLDING_STATUSES),
                EmailDraft.sent_at >= horizon_start,
            ),
        )
    )
    if exclude_draft_id is not None:
        stmt = stmt.where(EmailDraft.id != exclude_draft_id)

    slots: list[datetime] = []
    for scheduled_at, sent_at, status in db.execute(stmt):
        if status in _SLOT_HOLDING_STATUSES and scheduled_at is not None:
            slots.append(scheduled_at.astimezone(timezone.utc))
        if sent_at is not None and sent_at >= horizon_start:
            slots.append(sent_at.astimezone(timezone.utc))
    return slots


def allocate_scheduled_slot(
    db: Session,
    user_id: int,
    lead_tz: ZoneInfo,
    earliest: datetime,
    exclude_draft_id: int | None = None,
) -> datetime:
    """Next valid SCHEDULED slot: inside business hours (lead tz) and >= 2 minutes
    from every other dispatch for this user. Caller must hold the advisory lock."""
    occupied = occupied_slots(db, user_id, earliest - SCHEDULE_GAP, exclude_draft_id)
    return find_free_slot(earliest, occupied, SCHEDULE_GAP, snap=lambda c: snap_to_business_hours(c, lead_tz))


def allocate_send_slot(
    db: Session,
    user_id: int,
    now: datetime,
    exclude_draft_id: int | None = None,
) -> datetime:
    """Slot for a manual Send: ignores business hours, keeps only the 30-second gap.
    Returns `now` itself when nothing conflicts (dispatch inline immediately);
    otherwise the earliest instant >= now clear of all dispatches by 30s.
    Caller must hold the advisory lock."""
    occupied = occupied_slots(db, user_id, now - SEND_GAP, exclude_draft_id)
    return find_free_slot(now, occupied, SEND_GAP, snap=None)
