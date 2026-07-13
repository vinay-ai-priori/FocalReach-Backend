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

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company import Company
from app.models.company_intelligence import CompanyIntelligence
from app.models.email_draft import DraftStatus, EmailDraft
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
- If PREVIOUS DRAFTS are provided, write a fresh draft that does not repeat their exact sentences.

Return ONLY a JSON object:
{"subject": string, "body": string, "referenced_data": [string, string, ...]}
- body: plain text with real line breaks, following the structure above.
- referenced_data: 3-6 short bullet strings, each naming a specific fact you used and where it came from (e.g. "Lead company enrichment: Kentec manufactures life-safety detection systems", "ICP tone: consultative", "Location: United Kingdom -> British English"). Only list facts actually used."""

REFINE_INSTRUCTIONS: dict[str, str] = {
    "shorter": "Rewrite the CURRENT DRAFT to be meaningfully shorter (roughly two-thirds the length) while keeping the mandatory structure, all grounding rules, the same core facts, and the same ask.",
    "more_technical": "Rewrite the CURRENT DRAFT for a technical reader: use precise domain and technology terminology drawn from the provided data (never invented), and make the value explanation more concrete about how the solution works. Keep the mandatory structure and grounding rules.",
    "more_executive": "Rewrite the CURRENT DRAFT for a senior executive reader: lead with business outcomes and strategic value, minimize operational detail, keep it crisp and confident. Keep the mandatory structure and grounding rules.",
    "more_friendly": "Rewrite the CURRENT DRAFT in a warmer, more personable register while remaining professional. Keep the mandatory structure, all facts, and grounding rules.",
    "personalize_further": "Rewrite the CURRENT DRAFT using MORE specific facts from the recipient company's enrichment data than the current draft does — pull in additional concrete details (offerings, technologies, pain points) that are present in the data but unused. Keep the mandatory structure and grounding rules.",
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
        f"PREVIOUS DRAFTS IN THIS THREAD:\n{_previous_drafts_block(draft)}",
    ]
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
        data, was_cached = cached_json_completion(SYSTEM_PROMPT, user_prompt, skip_cache=mode != "initial")
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
