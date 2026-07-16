"""Extracts a proposed meeting date/time from a BOOKED-intent reply. Anchors relative
expressions ("Tuesday", "next Monday morning") to the email's OWN received timestamp,
not whenever the poller happens to process it — a queued/delayed poll run must not
shift what "Tuesday" meant to the prospect."""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones

from app.core.config import settings
from app.core.logging import get_logger
from app.services.ai.openai_client import cached_json_completion

logger = get_logger(__name__)

SYSTEM_PROMPT = """You extract a proposed meeting date and time from a prospect's email \
reply that is asking to schedule a call.

You are given the email's received timestamp (UTC) to anchor any relative date \
expression ("Tuesday", "next Monday", "tomorrow morning") against — treat that \
timestamp as "now" when resolving relative dates.

If the reply states or implies a timezone (e.g. "2pm IST", "10am EST", "my time, \
UTC+2"), convert it to its IANA timezone name (e.g. "Asia/Kolkata", "America/New_York") \
in the "timezone" field. If no timezone is stated or implied, set "timezone" to null.

Respond with strict JSON:
{"found": true|false, "date": "YYYY-MM-DD"|null, "time": "HH:MM"|null (24h), \
"timezone": "IANA name"|null, "confidence": 0.0-1.0}

Set "found": false if there is no usable specific date/time in the reply."""


@dataclass
class ExtractedDateTime:
    found: bool
    confidence: float
    date: str | None
    time: str | None
    timezone: str | None
    raw: dict


def extract_datetime(body_text: str, received_at: datetime) -> ExtractedDateTime:
    anchor = received_at.astimezone(timezone.utc).isoformat()
    user_prompt = f"Email received at (UTC): {anchor}\n\nReply:\n{body_text or '(empty body)'}"
    content, _ = cached_json_completion(SYSTEM_PROMPT, user_prompt, temperature=0.0)

    try:
        confidence = float(content.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0

    tz_name = content.get("timezone")
    if tz_name and tz_name not in available_timezones():
        tz_name = None

    return ExtractedDateTime(
        found=bool(content.get("found")) and confidence >= settings.REPLY_DATETIME_CONFIDENCE_THRESHOLD,
        confidence=confidence,
        date=content.get("date"),
        time=content.get("time"),
        timezone=tz_name,
        raw=content,
    )


def resolve_to_instant(extracted: ExtractedDateTime, fallback_tz: str | None) -> tuple[datetime | None, str | None, str]:
    """Returns (instant_utc, source_timezone_used, timezone_source) or (None, None,
    "unknown") if resolution isn't possible. source is "explicit" when the reply
    stated a timezone, "lead_country" when the fallback was used, "unknown" otherwise."""
    if not extracted.found or not extracted.date or not extracted.time:
        return None, None, "unknown"

    tz_name = extracted.timezone
    source = "explicit"
    if not tz_name:
        tz_name = fallback_tz
        source = "lead_country" if tz_name else "unknown"
    if not tz_name:
        return None, None, "unknown"

    try:
        tz = ZoneInfo(tz_name)
        naive = datetime.strptime(f"{extracted.date} {extracted.time}", "%Y-%m-%d %H:%M")
        local = naive.replace(tzinfo=tz)
        return local.astimezone(timezone.utc), tz_name, source
    except Exception:
        logger.warning("Could not resolve extracted date/time %r %r in tz %r", extracted.date, extracted.time, tz_name)
        return None, None, "unknown"
