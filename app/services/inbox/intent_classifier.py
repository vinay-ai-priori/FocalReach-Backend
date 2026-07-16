"""Classifies an inbound reply's intent. Deliberately a single fixed 4-way taxonomy —
see app/models/inbound_reply.py:ReplyIntent — with a confidence-gated fallback to
NEUTRAL for anything the model isn't sure about, since neutral is the safe "pause and
let a human look" outcome (no auto-send, unlike POSITIVE)."""

from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.models.inbound_reply import ReplyIntent
from app.services.ai.openai_client import cached_json_completion

logger = get_logger(__name__)

SYSTEM_PROMPT = """You classify a prospect's email reply to a sales outreach message into exactly \
one of four intents:

- "negative": the prospect clearly says they are not interested, asks to not be \
contacted again, or otherwise declines.
- "booked": the reply proposes or confirms a specific meeting date and/or time (even \
approximate, e.g. "Tuesday 2pm", "next Monday morning") and intends to schedule a call.
- "neutral": the prospect wants to be contacted later / isn't ready now (e.g. "reach \
out in a couple weeks", "check back next quarter", "I'm busy right now") — no clear \
decline, no scheduling intent yet.
- "positive": anything else showing genuine interest without a concrete date/time \
(e.g. "tell me more", "sounds interesting", "sure, let's talk").

Respond with strict JSON: {"intent": "negative"|"booked"|"neutral"|"positive", \
"confidence": 0.0-1.0, "reason": "one short sentence"}."""


@dataclass
class IntentResult:
    intent: ReplyIntent
    confidence: float
    reason: str
    raw_intent: str  # the model's verdict before any confidence-gated override


def classify_reply(subject: str | None, body_text: str) -> IntentResult:
    user_prompt = f"Subject: {subject or '(no subject)'}\n\nReply:\n{body_text or '(empty body)'}"
    content, _ = cached_json_completion(SYSTEM_PROMPT, user_prompt, temperature=0.0)

    raw_intent = str(content.get("intent", "")).strip().lower()
    try:
        confidence = float(content.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(content.get("reason", ""))[:1000]

    try:
        intent = ReplyIntent(raw_intent)
    except ValueError:
        logger.warning("Intent classifier returned an unrecognized intent %r — defaulting to neutral", raw_intent)
        return IntentResult(intent=ReplyIntent.NEUTRAL, confidence=confidence, reason=reason, raw_intent=raw_intent)

    if confidence < settings.REPLY_INTENT_CONFIDENCE_THRESHOLD:
        return IntentResult(intent=ReplyIntent.NEUTRAL, confidence=confidence, reason=reason, raw_intent=raw_intent)

    return IntentResult(intent=intent, confidence=confidence, reason=reason, raw_intent=raw_intent)
