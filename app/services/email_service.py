"""Email drafting: crawls the target company site (cached per domain), then uses AI to
draft a personalized first-touch email combining the user's company intelligence,
ICP tone, lead-company enrichment, prospect location (regional English), and any
previous drafts in the thread.

Modes:
- initial / regenerate: full draft in the fixed structure below.
- shorter / more_technical / more_executive / more_friendly / personalize_further:
  refine the CURRENT draft per the mode, keeping structure, tone, and grounding rules.

Fixed structure (every draft):
  Hello [lead first name],
  I hope you are doing well,
  Para 1 — about the lead's company (grounded in enrichment data).
  Para 2 — continues from Para 1; the sender's company and how it solves that problem.
  Para 3 — professional ask to share available times for a meeting (no booking links).
  Best regards,
  [sender name]
  [sender company]
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company import Company
from app.models.company_intelligence import CompanyIntelligence
from app.models.email_draft import (
    STEP_FOLLOW_UP_FIRST,
    STEP_FOLLOW_UP_LAST,
    DraftChannel,
    DraftStatus,
    EmailDraft,
)
from app.models.icp import ICP
from app.models.lead import Lead
from app.models.user import User
from app.services.ai.openai_client import cached_json_completion
from app.services.website.cache import get_cached_content, set_cached_content
from app.services.website.crawler import crawl_website
from app.services.website.url_validator import normalize_url

logger = get_logger(__name__)

REFINE_MODES = ("shorter", "more_technical", "more_executive", "more_friendly", "personalize_further")
ALL_MODES = ("initial", "regenerate", *REFINE_MODES)

# The first draft stays low-temperature (consistent, tightly grounded). Regenerate asks
# for a genuinely different take on the same brief, so it needs real variety. The
# reworded refine modes (more_technical/more_executive/more_friendly/personalize_further)
# still need some freedom to actually change, but less than regenerate since they're
# editing toward one specific dimension rather than rewriting freely. "shorter" is a trim,
# not a rewrite, so it stays close to the first draft's low temperature.
_TEMPERATURE_BY_MODE: dict[str, float] = {"initial": 0.2, "regenerate": 0.9, "shorter": 0.2}

# Countries whose business English follows British conventions (spelling like
# organise/optimise/colour, DD/MM dates, phrasing). Everything else defaults to US.
_BRITISH_ENGLISH_COUNTRIES = {
    "united kingdom", "uk", "great britain", "england", "scotland", "wales", "northern ireland",
    "ireland", "australia", "new zealand", "india", "singapore", "south africa", "hong kong",
    "malaysia", "pakistan", "bangladesh", "sri lanka", "nigeria", "kenya",
}


def _english_variant(lead: Lead, company: Company | None) -> tuple[str, str]:
    """(variant instruction, location used). Prefers the lead's own country, falls back
    to their company's country; US English when location is unknown."""
    location = (lead.country or (company.country if company else None) or "").strip()
    if location.lower() in _BRITISH_ENGLISH_COUNTRIES:
        return (
            f"Write in British English (recipient is in {location}): use -ise/-our/-re spellings "
            "(organise, optimise, colour, centre), British phrasing, and DD/MM date conventions.",
            location,
        )
    if location:
        return (
            f"Write in standard US English (recipient is in {location}): use -ize/-or/-er spellings "
            "(organize, optimize, color, center).",
            location,
        )
    return ("Write in standard US English (recipient location unknown).", "")


SYSTEM_PROMPT = """You write personalized B2B outreach emails. You MUST follow the exact structure and grounding rules below.

STRUCTURE (mandatory, in this order):
1. Greeting line: "Hello [recipient first name],"
2. Opening line: "I hope you are doing well,"
3. Paragraph 1 — about the RECIPIENT's company: reference concrete, specific facts from the provided enrichment data (what they do, their industry, technologies, or challenges). 2-3 sentences.
4. Paragraph 2 — flows naturally from paragraph 1: introduce the SENDER's company and explain specifically how it solves the problem or supports the priority identified in paragraph 1. 2-3 sentences.
5. Paragraph 3 — a professional closing ask: invite the recipient to share their available times to schedule a meeting. Do NOT include any booking links, URLs, or calendar links. 1-2 sentences.
6. Sign-off exactly:
Best regards,
[sender name]
[sender company]

GROUNDING RULES (mandatory):
- Use ONLY facts present in the provided data. Never invent customers, metrics, case studies, or claims.
- If enrichment data is thin, stay general about their industry rather than fabricating specifics.
- Match the requested TONE exactly.
- Follow the ENGLISH VARIANT instruction exactly (spelling and phrasing).
- If PREVIOUS DRAFTS are provided, write a fresh draft that does not repeat their exact sentences —
  UNLESS the REFINEMENT INSTRUCTION below says this is a trim/condense of the CURRENT DRAFT, in which
  case keep the CURRENT DRAFT's exact wording and only remove/shorten what the instruction says to.

Return ONLY a JSON object:
{"subject": string, "body": string, "referenced_data": [string, string, ...]}
- body: plain text with real line breaks, following the structure above.
- referenced_data: 3-6 short bullet strings, each naming a specific fact you used and where it came from (e.g. "Lead company enrichment: Kentec manufactures life-safety detection systems", "ICP tone: consultative", "Location: United Kingdom -> British English"). Only list facts actually used."""

FOLLOW_UP_SYSTEM_PROMPT = """You write personalized B2B follow-up emails. An earlier outreach email (and possibly earlier follow-ups) went out to this recipient and received no reply. You MUST follow the exact structure and grounding rules below.

STRUCTURE (mandatory, in this order):
1. Greeting line: "Hello [recipient first name],"
2. Opening line: a single short sentence that politely references the earlier email without guilt-tripping (e.g. acknowledging they are busy). Never say "just checking in" or "bumping this".
3. Paragraph 1 — add NEW value: a fresh, specific angle grounded in the provided data (a different offering, pain point, or priority of the RECIPIENT's company than previous touches used, and a different aspect of the SENDER's solution). 2-3 sentences.
4. Paragraph 2 — a professional closing ask: invite the recipient to share their available times for a short meeting. Do NOT include any booking links, URLs, or calendar links. 1 sentence.
5. Sign-off exactly:
Best regards,
[sender name]
[sender company]

FOLLOW-UP RULES (mandatory):
- Noticeably SHORTER than an initial email — 60-110 words of body text.
- The subject line continues the thread naturally (it may be "Re: <initial subject>" when an initial subject is provided).
- Do NOT repeat facts, angles, phrasing, or the value proposition already used in ANY previous touch listed — every previous touch counts as already said, whatever its channel.
- Later follow-ups escalate gently: follow-up 3 may note it is the last email and leave the door open.

GROUNDING RULES (mandatory):
- Use ONLY facts present in the provided data. Never invent customers, metrics, case studies, or claims.
- Match the requested TONE exactly. Follow the ENGLISH VARIANT instruction exactly.

Return ONLY a JSON object:
{"subject": string, "body": string, "referenced_data": [string, ...]}
- body: plain text with real line breaks, following the structure above.
- referenced_data: 3-6 short bullet strings naming the specific facts used and their source. Only list facts actually used."""

LINKEDIN_SYSTEM_PROMPT = """You write short, personalized LinkedIn outreach messages for B2B prospecting. This message follows earlier email outreach that received no reply — LinkedIn is a lighter, more personal channel.

RULES (mandatory):
- Maximum 500 characters total. No subject line concept — put a natural one-line hook first.
- Conversational and human, not salesy. No bullet points, no links, no booking/calendar links.
- Ground every claim ONLY in the provided data; never invent facts.
- Do NOT repeat the phrasing, facts, or angle of ANY previous touch listed — every previous touch counts as already said.
- Reference something specific about the RECIPIENT's company, connect it to the SENDER's company in one clause, and end with a soft ask (open to a short chat / exchanging a couple of messages). Do not ask for a meeting time slot — that was the emails' ask.
- Match the requested TONE and ENGLISH VARIANT exactly.

Return ONLY a JSON object:
{"subject": null, "body": string, "referenced_data": [string, ...]}
- body: the LinkedIn message as plain text.
- referenced_data: 2-5 short bullet strings naming the specific facts used and their source."""

CALL_SYSTEM_PROMPT = """You write concise, practical cold-call scripts for B2B sales development. The caller has already emailed (and possibly messaged on LinkedIn) this prospect without a reply.

STRUCTURE (mandatory, with these exact section headings):
OPENER: 1-2 sentences — name, company, and permission-based opener ("Did I catch you at a bad time?" style), acknowledging the earlier email briefly.
REASON FOR CALL: 2-3 sentences connecting a specific fact about the RECIPIENT's company to the SENDER's solution — use an angle NOT used in previous touches.
DISCOVERY QUESTIONS: 3 short, open questions grounded in the recipient company's context.
OBJECTION RESPONSES: the 2 most likely objections for this prospect, each with a 1-2 sentence response.
CLOSE: 1-2 sentences asking for a short follow-up meeting.

GROUNDING RULES (mandatory):
- Use ONLY facts present in the provided data. Never invent customers, metrics, case studies, or claims.
- Do NOT reuse the phrasing, facts, or angle of ANY previous touch listed — every previous touch counts as already said.
- Spoken register: short sentences, contractions, no jargon dumps. Match the requested TONE and ENGLISH VARIANT.

Return ONLY a JSON object:
{"subject": null, "body": string, "referenced_data": [string, ...]}
- body: the full script as plain text with the section headings above, real line breaks.
- referenced_data: 3-6 short bullet strings naming the specific facts used and their source."""


_REWORD_REQUIREMENT = (
    "The output MUST use noticeably different sentence wording and phrasing than the CURRENT DRAFT — "
    "meeting the instruction below is not enough on its own if you just lightly edit the existing sentences; "
    "rewrite them."
)

REFINE_INSTRUCTIONS: dict[str, str] = {
    # A trim, not a rewrite: keep the CURRENT DRAFT's exact sentences and wording, and
    # only cut redundant words/clauses or drop a less-essential sentence to hit roughly
    # two-thirds the length. Do NOT rephrase sentences that are kept as-is.
    "shorter": (
        "Condense the CURRENT DRAFT to roughly two-thirds its length. This is a TRIM, not a rewrite: "
        "reuse the CURRENT DRAFT's exact wording for every sentence you keep, and shorten only by "
        "removing redundant words/clauses or cutting a less-essential sentence — do not rephrase "
        "sentences that survive the cut. Keep the mandatory structure, all grounding rules, the same "
        "core facts, and the same ask."
    ),
    "more_technical": f"Rewrite the CURRENT DRAFT for a technical reader: use precise domain and technology terminology drawn from the provided data (never invented), and make the value explanation more concrete about how the solution works. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
    "more_executive": f"Rewrite the CURRENT DRAFT for a senior executive reader: lead with business outcomes and strategic value, minimize operational detail, keep it crisp and confident. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
    "more_friendly": f"Rewrite the CURRENT DRAFT in a warmer, more personable register while remaining professional. Keep the mandatory structure, all facts, and grounding rules. {_REWORD_REQUIREMENT}",
    "personalize_further": f"Rewrite the CURRENT DRAFT using MORE specific facts from the recipient company's enrichment data than the current draft does — pull in additional concrete details (offerings, technologies, pain points) that are present in the data but unused. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
}


def _enrich_company(company: Company) -> str:
    """Crawl the target company's website once and cache it (Redis + DB row)."""
    if company.enrichment_content:
        return company.enrichment_content
    if not company.website:
        return ""
    domain = company.domain or ""
    cached = get_cached_content(domain) if domain else None
    if cached:
        return cached.get("content", "")
    try:
        result = crawl_website(normalize_url(company.website))
        if domain:
            set_cached_content(domain, {"content": result.content[:20000]})
        return result.content
    except Exception as exc:
        logger.warning("Enrichment crawl failed for %s: %s", company.website, exc)
        return ""


STEP_LABELS = {
    1: "Initial email",
    2: "Follow-up 1",
    3: "Follow-up 2",
    4: "Follow-up 3",
    5: "LinkedIn message",
    6: "Call script",
}


def _system_prompt_for(draft: EmailDraft) -> str:
    if draft.channel == DraftChannel.LINKEDIN:
        return LINKEDIN_SYSTEM_PROMPT
    if draft.channel == DraftChannel.CALL:
        return CALL_SYSTEM_PROMPT
    if STEP_FOLLOW_UP_FIRST <= draft.step_index <= STEP_FOLLOW_UP_LAST:
        return FOLLOW_UP_SYSTEM_PROMPT
    return SYSTEM_PROMPT


def _prior_touches_block(db: Session, draft: EmailDraft) -> str:
    """Every OTHER step already written for this lead, any channel, ordered by sequence
    position. This is the 'already said — don't repeat it' context for follow-ups,
    LinkedIn, and call scripts."""
    others = db.scalars(
        select(EmailDraft)
        .where(EmailDraft.lead_id == draft.lead_id, EmailDraft.id != draft.id, EmailDraft.body.is_not(None))
        .order_by(EmailDraft.step_index)
    ).all()
    if not others:
        return "None."
    lines = []
    for other in others:
        label = STEP_LABELS.get(other.step_index, f"Step {other.step_index}")
        status = "sent" if other.status == DraftStatus.SENT else "drafted"
        lines.append(f"--- {label} ({other.channel.value}, {status}) ---")
        if other.subject:
            lines.append(f"Subject: {other.subject}")
        lines.append(other.body or "")
    return "\n".join(lines)


def _previous_drafts_block(draft: EmailDraft) -> str:
    """Previous versions in this thread (history + the current body when refining)."""
    versions = list(draft.history or [])
    if not versions:
        return "None."
    lines = []
    for i, v in enumerate(versions[-3:], 1):  # last 3 is plenty of context
        lines.append(f"--- Previous draft {i} (refined with: {v.get('refined_with', 'initial')}) ---")
        lines.append(f"Subject: {v.get('subject') or ''}")
        lines.append(v.get("body") or "")
    return "\n".join(lines)


def generate_email_draft(
    db: Session,
    draft: EmailDraft,
    lead: Lead,
    company: Company,
    icp: ICP,
    intelligence: CompanyIntelligence,
    sender: User | None = None,
    mode: str = "initial",
) -> EmailDraft:
    if mode not in ALL_MODES:
        mode = "initial"
    is_refine = mode in REFINE_MODES

    # Refining or regenerating supersedes the current version — keep it as thread context.
    if (mode == "regenerate" or is_refine) and draft.body:
        draft.history = [*(draft.history or []), {
            "subject": draft.subject, "body": draft.body, "refined_with": mode,
        }][-10:]

    draft.status = DraftStatus.GENERATING
    db.commit()

    enrichment = _enrich_company(company)
    if enrichment and not company.enrichment_content:
        company.enrichment_content = enrichment[:20000]
        db.commit()

    sender_name = (sender.full_name if sender else None) or "The team"
    sender_company = intelligence.company_name or "our company"
    variant_instruction, location = _english_variant(lead, company)

    sections = [
        f"TONE (from ICP): {icp.outreach_tone}",
        f"CAMPAIGN OBJECTIVE: {icp.campaign_objective}",
        f"ENGLISH VARIANT: {variant_instruction}",
        "",
        "SENDER:",
        f"- Name: {sender_name}",
        f"- Company: {sender_company}",
        f"- What the company does: {intelligence.summary}",
        f"- Key services: {[s.get('name') for s in (intelligence.services or [])][:5]}",
        f"- Value propositions: {intelligence.value_propositions}",
        "",
        "RECIPIENT:",
        f"- Name: {lead.full_name} (first name: {lead.first_name or lead.full_name.split()[0]})",
        f"- Title: {lead.title}",
        f"- Location: {location or 'unknown'}",
        f"- Company: {company.name} ({company.industry or 'industry unknown'}, "
        f"{company.employee_count or company.employee_range or 'size unknown'} employees, "
        f"{company.country or 'location unknown'})",
        f"- Company description: {company.description or 'N/A'}",
        f"- Structured profile from their website: {company.enrichment_profile or 'Not available'}",
        "",
        f"RECIPIENT COMPANY WEBSITE CONTENT (for personalization):\n{enrichment[:8000] or 'Not available'}",
        "",
        f"PREVIOUS OUTREACH TOUCHES TO THIS RECIPIENT (already said — do not repeat their facts, angles, or phrasing):\n{_prior_touches_block(db, draft)}",
        "",
        f"PREVIOUS DRAFTS OF THIS STEP:\n{_previous_drafts_block(draft)}",
    ]
    if draft.channel == DraftChannel.EMAIL and STEP_FOLLOW_UP_FIRST <= draft.step_index <= STEP_FOLLOW_UP_LAST:
        sections.insert(0, f"THIS IS {STEP_LABELS[draft.step_index].upper()} OF 3 IN THE SEQUENCE.")
    if is_refine:
        sections += [
            "",
            "CURRENT DRAFT (the one to refine):",
            f"Subject: {draft.subject or ''}",
            draft.body or "",
            "",
            f"REFINEMENT INSTRUCTION: {REFINE_INSTRUCTIONS[mode]}",
        ]
    user_prompt = "\n".join(sections)

    try:
        # Regenerate/refine must produce a NEW draft, so bypass the response cache for
        # everything except the very first generation.
        temperature = _TEMPERATURE_BY_MODE.get(mode, 0.7)  # refine modes default to 0.7
        data, was_cached = cached_json_completion(
            _system_prompt_for(draft), user_prompt, skip_cache=mode != "initial", temperature=temperature
        )
        referenced = data.get("referenced_data") or []
        if isinstance(referenced, list):
            referenced = [str(r).strip() for r in referenced if str(r).strip()]
        else:
            referenced = [str(referenced)]
        draft.subject = data.get("subject")
        draft.body = data.get("body")
        draft.personalization_notes = " ".join(
            note if note.endswith((".", "!", "?")) else f"{note}." for note in referenced
        )
        draft.booking_link = None  # spec: no booking/calendar links in drafts
        draft.ai_model = settings.OPENAI_MODEL
        draft.ai_cached = was_cached
        draft.status = DraftStatus.READY
    except Exception as exc:
        draft.status = DraftStatus.FAILED
        draft.error_message = str(exc)[:1000]
        logger.exception("Email draft %s failed (mode=%s)", draft.id, mode)
    db.commit()
    db.refresh(draft)
    return draft
