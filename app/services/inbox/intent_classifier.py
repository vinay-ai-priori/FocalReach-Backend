"""Classifies an inbound reply into one of two intents (see
app/models/inbound_reply.py:ReplyIntent):

- BOOKING_PENDING — the reply names a concrete date, day, or time to meet.
- NEED_REPLY — anything else; a human needs to write back.

Detection is deterministic first: a regex scan for date/day/time tokens decides the
overwhelming majority of replies without any model call. The LLM is only invoked when
the wording is genuinely ambiguous — the deterministic pass finds a scheduling token
but can't tell whether it actually proposes a meeting time (e.g. "I've been here 3
years", "call me on my cell 2pm-ish maybe"). This mirrors the product copy: "AI is
only invoked when the wording is genuinely ambiguous.\""""

import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.models.inbound_reply import ReplyIntent
from app.services.ai.openai_client import cached_json_completion

logger = get_logger(__name__)

# --- Deterministic date/day/time detection -------------------------------------

_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
_MONTHS = (
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

# A clock time: "3pm", "3 pm", "3:30pm", "15:00", "10.30am".
_TIME = re.compile(r"\b((?:1[0-2]|0?[1-9])(?:[:.][0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|(?:[01]?\d|2[0-3]):[0-5]\d)\b", re.I)
# A weekday or relative day reference.
_DAY = re.compile(rf"\b({_WEEKDAYS}|today|tomorrow|tonight)\b", re.I)
_RELATIVE = re.compile(r"\b(next|this|coming)\s+(week|" + _WEEKDAYS + r")\b", re.I)
# A calendar date: "July 7", "7 July", "on the 15th", "2025-07-07", "07/07".
_DATE = re.compile(
    rf"\b((?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})|"
    r"the\s+\d{1,2}(?:st|nd|rd|th)|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b",
    re.I,
)


@dataclass
class IntentResult:
    intent: ReplyIntent
    confidence: float
    reason: str
    detection: str  # "deterministic" or "ai" — how the verdict was reached


def _deterministic_signals(text: str) -> dict[str, bool]:
    return {
        "time": bool(_TIME.search(text)),
        "day": bool(_DAY.search(text)),
        "relative": bool(_RELATIVE.search(text)),
        "date": bool(_DATE.search(text)),
    }


AMBIGUITY_PROMPT = """A prospect replied to a sales outreach email. Decide whether the \
reply proposes or confirms a specific meeting date, day, or time to schedule a call.

Answer true only if there is a real scheduling proposal (e.g. "Let's meet Tuesday at \
3pm", "how about next week?", "the 15th works"). Answer false if any date/day/time \
words are incidental and not a meeting proposal (e.g. "I've been in this role 2 years", \
"talk to you soon", "not interested").

Respond with strict JSON: {"booking": true|false, "confidence": 0.0-1.0, \
"reason": "one short sentence"}."""


def _classify_with_ai(subject: str | None, body_text: str) -> IntentResult:
    user_prompt = f"Subject: {subject or '(no subject)'}\n\nReply:\n{body_text or '(empty body)'}"
    content, _ = cached_json_completion(AMBIGUITY_PROMPT, user_prompt, temperature=0.0)
    try:
        confidence = float(content.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(content.get("reason", ""))[:1000]
    booking = bool(content.get("booking")) and confidence >= settings.REPLY_INTENT_CONFIDENCE_THRESHOLD
    return IntentResult(
        intent=ReplyIntent.BOOKING_PENDING if booking else ReplyIntent.NEED_REPLY,
        confidence=confidence,
        reason=reason or ("Model saw a scheduling proposal." if booking else "No scheduling proposal found."),
        detection="ai",
    )


def classify_reply(subject: str | None, body_text: str) -> IntentResult:
    text = body_text or ""
    signals = _deterministic_signals(text)

    has_time = signals["time"]
    has_day = signals["day"] or signals["relative"] or signals["date"]

    # Confident BOOKING_PENDING: a concrete time, or a specific date/day. Both make the
    # scheduling intent unambiguous enough to skip the model.
    if has_time or signals["date"]:
        hits = ", ".join(k for k, v in signals.items() if v)
        return IntentResult(
            intent=ReplyIntent.BOOKING_PENDING,
            confidence=1.0,
            reason=f"Found scheduling token(s): {hits}.",
            detection="deterministic",
        )

    # A bare weekday/relative-day mention with no time is the genuinely ambiguous case
    # ("catch you Monday" vs "Monday I was out sick") — let the model arbitrate.
    if has_day:
        return _classify_with_ai(subject, text)

    # No date/day/time tokens at all — deterministically NEED_REPLY, no model call.
    return IntentResult(
        intent=ReplyIntent.NEED_REPLY,
        confidence=1.0,
        reason="No date, day, or time proposed.",
        detection="deterministic",
    )
