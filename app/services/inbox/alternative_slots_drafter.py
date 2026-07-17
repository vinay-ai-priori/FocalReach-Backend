"""Drafts the "that time isn't available — here are some alternatives" reply sent to
a lead whose requested meeting slot couldn't be booked (outside working hours, or the
slot was already taken). Invoked by the booking orchestrator; the email itself is
dispatched through the normal collision-safe scheduling pathway."""

from dataclasses import dataclass

from app.core.exceptions import ExternalServiceError
from app.services.ai.openai_client import cached_json_completion

SYSTEM_PROMPT = """You write a short, warm, professional reply to a prospect who asked to \
book a call at a specific time, but that time is not available on the sender's calendar.

The email must:
- Briefly acknowledge and thank them for proposing a time.
- Say that unfortunately that specific slot isn't available.
- Offer the provided alternative time slots, listed clearly one per line exactly as given \
(do not invent, reword, reorder, or drop any slot — copy each one verbatim).
- Ask them to reply with whichever option works (or propose another time).
- Be at most 120 words, no placeholder text like [Name], no signature block.

Respond with strict JSON: {"subject": "...", "body": "..."}. If a reply subject is \
provided, reuse it unchanged."""


@dataclass
class AlternativeSlotsDraft:
    subject: str
    body: str


def draft_alternative_slots_email(
    *,
    lead_name: str | None,
    requested_time_display: str,
    slot_displays: list[str],
    reply_subject: str | None,
) -> AlternativeSlotsDraft:
    """slot_displays are pre-formatted strings in the LEAD's timezone (e.g.
    'Monday, Jul 20 at 2:30 PM IST') — the model must echo them verbatim."""
    slots_block = "\n".join(f"- {s}" for s in slot_displays)
    user_prompt = (
        f"Prospect name: {lead_name or '(unknown)'}\n"
        f"Time they asked for: {requested_time_display}\n"
        f"Reply subject to reuse: {reply_subject or '(none — write a short one)'}\n\n"
        f"Available alternative slots (offer exactly these, verbatim):\n{slots_block}"
    )
    content, _ = cached_json_completion(SYSTEM_PROMPT, user_prompt, temperature=0.2)

    subject = str(content.get("subject") or "").strip()
    body = str(content.get("body") or "").strip()
    if not subject or not body:
        raise ExternalServiceError("Alternative-slots drafter returned an empty subject or body.")
    # Hard guarantee regardless of model behavior: every offered slot appears verbatim.
    missing = [s for s in slot_displays if s not in body]
    if missing:
        body = f"{body}\n\nAvailable times:\n{slots_block}"
    return AlternativeSlotsDraft(subject=subject[:500], body=body)
