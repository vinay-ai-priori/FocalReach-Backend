"""Classifies an inbound reply into one of two intents (see
app/models/inbound_reply.py:ReplyIntent):

- BOOKING_PENDING — the sender actually wants to schedule/confirm a meeting.
- NEED_REPLY — anything else; a human needs to write back.

Classification is intent-based: the model reads the WHOLE reply and judges what the
sender wants. It deliberately does NOT key off the mere presence of a date/day/time
token — those routinely appear incidentally (quoted "On <date> … wrote:" attribution
lines, "I've been here 3 years", "talk to you next week maybe") and were previously
flipping plain replies to BOOKING_PENDING. A concrete time is a supporting signal the
model may weigh, never the verdict on its own. The only non-model path is an empty
body, which is trivially NEED_REPLY.
"""

from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.models.inbound_reply import ReplyIntent
from app.services.ai.openai_client import cached_json_completion

logger = get_logger(__name__)


@dataclass
class IntentResult:
    intent: ReplyIntent
    confidence: float
    reason: str
    detection: str  # "ai" or "deterministic" — how the verdict was reached


INTENT_PROMPT = """You are triaging a prospect's reply to a sales outreach email. Read \
the ENTIRE reply and judge the sender's actual intent.

Set "booking" to true ONLY when the sender genuinely wants to schedule or confirm a \
meeting or call — e.g. proposing a time, accepting/confirming one, or asking to set one \
up ("Let's talk Tuesday at 3pm", "how about next week?", "sure, happy to hop on a call"). \
This is about intent, not keywords: a date, day, or time may appear incidentally — in \
quoted thread text, an email signature, or a phrase like "I've been in this role 2 years" \
— and must NOT count as a booking on its own.

Set "booking" to false for everything else, where a human needs to respond: questions, \
objections, "not interested", "tell me more", "who is this?", out-of-office replies, or \
general small talk — even if they contain a date, day, or time.

Respond with strict JSON: {"booking": true|false, "confidence": 0.0-1.0, \
"reason": "one short sentence"}."""


def _classify_with_ai(subject: str | None, body_text: str) -> IntentResult:
    user_prompt = f"Subject: {subject or '(no subject)'}\n\nReply:\n{body_text or '(empty body)'}"
    content, _ = cached_json_completion(INTENT_PROMPT, user_prompt, temperature=0.0)
    try:
        confidence = float(content.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(content.get("reason", ""))[:1000]
    booking = bool(content.get("booking")) and confidence >= settings.REPLY_INTENT_CONFIDENCE_THRESHOLD
    return IntentResult(
        intent=ReplyIntent.BOOKING_PENDING if booking else ReplyIntent.NEED_REPLY,
        confidence=confidence,
        reason=reason or ("Sender wants to schedule a meeting." if booking else "No scheduling intent."),
        detection="ai",
    )


def classify_reply(subject: str | None, body_text: str) -> IntentResult:
    text = (body_text or "").strip()
    if not text:
        # Nothing to interpret — no model call needed.
        return IntentResult(
            intent=ReplyIntent.NEED_REPLY,
            confidence=1.0,
            reason="Empty reply body.",
            detection="deterministic",
        )
    return _classify_with_ai(subject, text)
