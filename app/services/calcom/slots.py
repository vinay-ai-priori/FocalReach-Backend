"""Pure helpers for working with Cal.com slot payloads."""

from datetime import datetime, timezone
from typing import Any


def parse_slot_start(value: Any) -> datetime | None:
    """Parses a slot's start into an aware UTC datetime, or None when it can't be
    parsed. Cal.com may return offsets ('2026-07-16T09:00:00+05:30') or Zulu
    ('...T09:00:00Z') depending on endpoint/version — lexicographic string
    comparison across those formats is wrong, so everything is normalized here."""
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Cal.com always sends offsets with the timeZone param, but if one ever
        # arrives naive, treating it as UTC beats crashing the whole slot list.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def filter_future_slots(raw_slots: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Keeps only slots strictly after `now` (an aware datetime), dropping any slot
    whose start is missing or unparseable, ordered soonest-first."""
    keyed = []
    for slot in raw_slots:
        start = parse_slot_start(slot.get("start"))
        if start is not None and start > now:
            keyed.append((start, slot))
    keyed.sort(key=lambda pair: pair[0])
    return [slot for _, slot in keyed]
