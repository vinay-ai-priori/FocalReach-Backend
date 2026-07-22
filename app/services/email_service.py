"""Email drafting: crawls the target company site (cached per domain), then uses AI to
draft a personalized first-touch email combining the user's company intelligence,
ICP tone, lead-company enrichment, prospect location (regional English), and any
previous drafts in the thread.

Modes:
- initial: full draft in the pain-hook structure below, built around exactly ONE primary
  pain chosen by matching ICP intent keywords against the lead company's data.
- regenerate: a fresh full rewrite of the SAME argument — same primary pain, same proof
  point, same CTA objective; only wording/structure/emphasis change.
- shorter / more_technical / more_executive / more_friendly / personalize_further:
  refine the CURRENT draft per the mode, keeping the same primary pain and proof point
  (personalize_further may add one new supporting fact anchored to that same pain).

Initial-email structure:
  Subject: <pain hook> at <company>
  Hey <lead first name>,
  Personalization line (person-specific if the lead matches scraped people[], else a
  company-level news/signal).
  Pain statement (1-2 lines, the primary pain their role/industry faces).
  Proof point (one stat or one-line result from sender data — never invented).
  CTA: the campaign objective as a short question for <company> (no booking links).
  <sign-off>,
  <sender first name>

Follow-up, LinkedIn, and call-script prompts are unchanged by the pain-hook template.
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
    STEP_INITIAL,
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

# Initial-email modes that only rework what is already in the CURRENT DRAFT (pain, proof,
# and CTA are locked to it), so they get a slim prompt: draft + tone + english variant +
# names only. more_technical and personalize_further stay on the full prompt — they must
# pull domain terminology / new facts from the enrichment and sender data.
_SLIM_REFINE_MODES = ("regenerate", "shorter", "more_executive", "more_friendly")

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


SYSTEM_PROMPT = """You write personalized B2B cold outreach emails built around exactly ONE primary pain point. You MUST follow the exact structure, pain rules, and grounding rules below.

CHOOSING THE PRIMARY PAIN (do this first):
- Pick ONE pain from the SOLVABLE PAIN POINTS list — the one most relevant to the recipient's role and seniority. If that list is empty, derive the pain from the ICP intent keywords and the company data instead.
- Exactly one primary pain per email. Never blend two pains.

SUBJECT (mandatory format): "<pain hook> at <recipient company name>" — the pain hook is a short (2-5 word) phrase naming the primary pain (e.g. "Unplanned downtime at Kentec"). It must name the PAIN, not a vague benefit ("Enhanced operational efficiency" is wrong). No clickbait, no ALL CAPS, no emojis.

BODY STRUCTURE (mandatory, in this order — the body MUST contain exactly 4 separate paragraphs between the greeting and the sign-off: opener, pain, solution, CTA. Never merge two of them into one paragraph):
1. Greeting line: "Hey <recipient first name>,"
2. Personalization opener (1-2 sentences): grounded in something OBSERVED, never in the recipient's job title alone. If a PERSON MATCH is provided, open with that person-specific detail (their bio, program, or how their site mentions them). Otherwise open with a company-level signal — a NEWS item or a concrete fact from the company overview. Restating their title back at them ("As the Director of X, your role is pivotal...") is NOT personalization and is forbidden as an opener. This opener MUST be its own paragraph, separate from the pain paragraph. Only if there is no person match AND no company-level signal at all may you skip it and open with the pain paragraph.
3. Pain paragraph (2-3 sentences): state the primary pain AND ground it in its evidence — why THIS company specifically faces it (cite the evidence from the pain point data). Connect it to what the RECIPIENT deals with in their role (use their title, seniority, or time in role where it fits naturally). Written about their world, not as a pitch.
4. Solution paragraph (2-3 sentences): introduce the SENDER's company and the specific service that addresses that pain, explain briefly HOW it addresses it, and include ONE proof point drawn ONLY from the sender's provided data (a stat like "cut unplanned downtime by up to 30%" ONLY if that figure appears in the data; otherwise a qualitative result). NEVER invent a statistic. If the data names no customers or case studies, describe the capability only — phrases implying an existing customer base or track record ("we've seen clients...", "our customers...", "companies like yours", "helping companies like yours") are forbidden.
5. Soft CTA (1 sentence, phrased as a question): a low-pressure ask to CONNECT or continue the conversation — gauge interest, offer to share more (e.g. "Would this be worth a conversation for <company>?" or "Happy to share how this could apply to <company> — interested?"). Do NOT ask for a meeting, a call, or a time commitment ("15-minute chat", "schedule a call", "share your availability" are all forbidden in this email). Do NOT include any booking links, URLs, or calendar links.
6. Sign-off:
<short sign-off matching the tone (e.g. "Best," or "Cheers,")>
<sender first name>

GROUNDING RULES (mandatory):
- Use ONLY facts present in the provided data. Never invent customers, metrics, case studies, or claims.
- FORBIDDEN unless the data names actual customers: "we've seen clients", "our clients", "our customers", "companies like yours", "teams like yours", or ANY sentence implying an existing customer base or track record. When in doubt, state what the product does — not who has used it.
- If enrichment data is thin, stay general about their industry rather than fabricating specifics.
- Match the requested TONE exactly.
- Follow the ENGLISH VARIANT instruction exactly (spelling and phrasing).
- If a REGENERATE or REFINEMENT INSTRUCTION is provided below, it is binding: keep the SAME primary pain, the SAME proof point, and the SAME CTA objective as the CURRENT DRAFT — never swap the primary pain for a different one — and change only what the instruction says to change.
- If PREVIOUS DRAFTS are provided, do not repeat their exact sentences — UNLESS the instruction says this is a trim/condense of the CURRENT DRAFT, in which case keep the CURRENT DRAFT's exact wording and only remove/shorten.

Return ONLY a JSON object with EXACTLY these four keys IN THIS ORDER — ALL FOUR ARE REQUIRED, never omit any of them:
{"primary_pain": string, "subject": string, "referenced_data": [string, string, ...], "body": string}
- primary_pain: REQUIRED and FIRST — a short phrase naming the ONE primary pain this email is built on.
- body: plain text with real line breaks, following the structure above.
- referenced_data: 3-6 short bullet strings, each naming a specific fact you used and where it came from (e.g. "Intent keyword match: 'downtime' -> solvable pain point on unplanned outages", "Sender value proposition: reduces manual reporting effort", "Location: United Kingdom -> British English"). Only list facts actually used."""

FOLLOW_UP_SYSTEM_PROMPT = """You write personalized B2B follow-up emails. An earlier outreach email (and possibly earlier follow-ups) went out to this recipient and received no reply. A follow-up is a CONTINUATION of that same conversation — never a fresh pitch. You MUST follow the exact structure and grounding rules below.

CONTINUATION PRINCIPLE (the most important rule):
- Every follow-up continues the SAME single primary pain the initial email was built on (given below as THREAD PRIMARY PAIN, and visible in the previous touches). NEVER introduce a new or different pain point, and NEVER pitch a different aspect of the solution as if starting over.
- Build naturally on what was already said, as the next message in one ongoing thread. You SHOULD stay on the same pain — but you must NOT restate it in the same words or reuse the same supporting detail. Add ONE fresh supporting angle, proof point, or framing on that SAME pain each time so each email reads like a genuine next nudge, not a copy of the last.

STRUCTURE (mandatory, in this order):
1. Greeting line: "Hello [recipient first name],"
2. Opening line: a single short sentence that continues the thread and politely references the earlier email without guilt-tripping (e.g. acknowledging they are likely busy). Never say "just checking in" or "bumping this".
3. Body: continue the SAME primary pain with one fresh supporting angle or proof, then a professional low-pressure ask. Keep it tight — see the per-step objective below for exactly how this step should read.
4. Sign-off exactly:
Best regards,
[sender name]
[sender company]

PER-STEP OBJECTIVE (the user prompt states which follow-up this is — follow it exactly):
- Follow-up 1: a brief, warm bump on the same pain. Add one new supporting detail or angle on that pain, then softly invite a short conversation.
- Follow-up 2: continue the same pain from a DIFFERENT supporting angle or proof than follow-up 1 used. Slightly more direct about the value of a quick chat, still respectful.
- Follow-up 3 (the graceful step-back / final email): acknowledge you've reached out a couple of times and that the timing may just be busy for them, and gracefully step back — make clear you won't keep emailing. Keep it warm and professional (marketing tone, not resentful). Still tie it to the SAME pain in one short line and leave a soft, standing invitation to reach out whenever the timing is right. Example of the intended tone (do NOT copy verbatim; personalize it): "I've reached out a couple of times about <the pain> — it looks like now may just not be the right moment, and that's completely fine. I'll leave this here; whenever <the pain> becomes a priority, just reply and I'll be glad to help."

FOLLOW-UP RULES (mandatory):
- Noticeably SHORTER than an initial email — 55-100 words of body text.
- The subject line continues the thread naturally (it may be "Re: <initial subject>" when an initial subject is provided).
- Do NOT reuse the phrasing or the specific supporting detail of ANY previous touch listed. Staying on the SAME primary pain is REQUIRED; only the wording, angle, and proof must be fresh.
- No booking links, URLs, or calendar links anywhere.

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

# One pain per lead across the whole sequence: every refine/regenerate keeps the CURRENT
# DRAFT's primary pain, proof point, and CTA objective fixed. Only personalize_further may
# ADD a new supporting fact, and even it may not introduce a competing pain.
_PAIN_LOCK = (
    "Keep the CURRENT DRAFT's primary pain, proof point, and the CTA's underlying objective EXACTLY the "
    "same — do NOT swap the primary pain for a different one, even if other pain points are available in "
    "the data. Restate the proof point at the SAME claim strength as the CURRENT DRAFT: do not add "
    "evidential framing such as 'has been shown to', 'proven', or 'has demonstrated' unless those words "
    "are already in the CURRENT DRAFT. ALWAYS keep the greeting line and the sign-off lines "
    "(sign-off word + sender first name) — they must never be cut."
)

# Regenerate = "another draft of the same argument", not "a different argument": same
# pain, same proof, same CTA objective — fresh full rewrite of the wording/structure.
REGENERATE_INSTRUCTION = (
    f"Produce a fresh full rewrite of the CURRENT DRAFT. {_PAIN_LOCK} "
    "Re-write the wording, sentence construction, structure, and emphasis around those same facts — a "
    "different opening angle on the SAME personalization signal, tighter or looser phrasing — so it reads "
    "as another draft of the same argument, never a different argument. Do not add new facts and do not "
    f"drop the existing ones. {_REWORD_REQUIREMENT}"
)

REFINE_INSTRUCTIONS: dict[str, str] = {
    # A trim, not a rewrite: keep the CURRENT DRAFT's exact sentences and wording, and
    # only cut redundant words/clauses or drop a less-essential sentence to hit roughly
    # two-thirds the length. Do NOT rephrase sentences that are kept as-is.
    "shorter": (
        f"Condense the CURRENT DRAFT, cutting to the core ask. {_PAIN_LOCK} "
        "NEVER delete the greeting line ('Hey <first name>,') or the sign-off lines — the output MUST still "
        "start with the same greeting including the recipient's first name, and end with the same sign-off "
        "and sender first name. "
        "This is a TRIM, not a rewrite: keep the CURRENT DRAFT's words in their existing order and shorten "
        "ONLY by deleting — never by substituting synonyms or reordering. Go sentence by sentence and strip "
        "everything non-essential: softeners and filler openers ('I noticed that', 'I can imagine', 'I hope'), "
        "adjectives and adverbs ('exciting', 'significantly', 'particularly'), and redundant clauses that "
        "restate what another sentence already says. Every structural line must end up leaner. HARD TARGET: "
        "the output body MUST have at LEAST 25% fewer words than the CURRENT DRAFT — count both and keep "
        "deleting until you are under. The core ask is what must survive: the pain statement, the proof "
        "point, and the CTA. To hit the target you SHOULD cut the personalization line down to a short "
        "clause merged into the pain statement, or drop it entirely — for this mode only, that structure "
        "exemption is allowed. Keep all remaining facts and all grounding rules."
    ),
    "more_technical": f"Rewrite the CURRENT DRAFT for a technical reader. {_PAIN_LOCK} Swap generic language for precise domain and technology terminology drawn from the provided data (never invented), and make the value explanation more concrete about how the solution works. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
    "more_executive": f"Rewrite the CURRENT DRAFT for a senior executive reader. {_PAIN_LOCK} Reframe the same pain and proof around business outcome, strategic value, and ROI rather than mechanism; minimize operational detail; keep it crisp and confident. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
    "more_friendly": f"Rewrite the CURRENT DRAFT in a warmer, more personable register while remaining professional. {_PAIN_LOCK} Use a more casual sign-off that still fits the tone. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
    "personalize_further": f"Rewrite the CURRENT DRAFT adding MORE specific personalization from the recipient company's enrichment data. {_PAIN_LOCK} You MAY add one new supporting fact (e.g. a secondary signal, or a short P.S. line) that is present in the data but unused — but it must reinforce the SAME primary pain, never introduce a competing pain. Keep the mandatory structure and grounding rules. {_REWORD_REQUIREMENT}",
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

# Per-follow-up intent, injected into the user prompt so each step reads as the next
# message in one thread on the SAME pain — not a fresh pitch. Keyed by step_index.
_FOLLOW_UP_OBJECTIVES = {
    STEP_FOLLOW_UP_FIRST: (
        "A brief, warm bump on the same pain from the initial email. Add ONE fresh supporting "
        "detail or angle on that pain, then softly invite a short conversation."
    ),
    STEP_FOLLOW_UP_FIRST + 1: (
        "Continue the same pain from a DIFFERENT supporting angle or proof than Follow-up 1 used. "
        "Be slightly more direct about the value of a quick chat, still respectful."
    ),
    STEP_FOLLOW_UP_LAST: (
        "The graceful step-back / final email. Acknowledge you've reached out a couple of times and "
        "the timing may just be busy, and make clear you won't keep emailing — warm and professional, "
        "never resentful. Tie it to the SAME pain in one short line and leave a soft, standing "
        "invitation to reach out whenever the timing is right. Keep a low-pressure CTA."
    ),
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


def _thread_primary_pain(db: Session, draft: EmailDraft) -> str:
    """The one primary pain the thread's initial email was built on — follow-ups must
    continue THIS pain, never a new one. Read from the initial (step 1) draft's stored
    personalization notes, where _complete_draft prefixes 'Primary pain: <pain>.'."""
    initial = db.scalars(
        select(EmailDraft).where(
            EmailDraft.lead_id == draft.lead_id,
            EmailDraft.channel == DraftChannel.EMAIL,
            EmailDraft.step_index == STEP_INITIAL,
            EmailDraft.personalization_notes.is_not(None),
        )
    ).first()
    notes = (initial.personalization_notes if initial else None) or ""
    for part in notes.split("."):
        part = part.strip()
        if part.lower().startswith("primary pain:"):
            return part.split(":", 1)[1].strip()
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


def _sender_first_name(sender_name: str) -> str:
    """First name for the sign-off, skipping leading initials: 'K Vinay Kumar Reddy'
    signs as 'Vinay', not 'K'."""
    tokens = [t for t in sender_name.split() if t]
    for token in tokens:
        if len(token.rstrip(".")) > 1:
            return token
    return tokens[0] if tokens else sender_name


def _matched_person(lead: Lead, profile: dict) -> dict | None:
    """Code-side lead-vs-scraped-people match, so the model never has to guess."""
    lead_full = (lead.full_name or "").strip().lower()
    first = (lead.first_name or "").strip().lower()
    last = (lead.last_name or "").strip().lower()
    for p in profile.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip().lower()
        if not name:
            continue
        if name == lead_full or (first and last and first in name and last in name):
            return {"name": p.get("name"), "title": p.get("title"), "bio_snippet": p.get("bio_snippet")}
    return None


def _company_overview(company: Company, profile: dict) -> str:
    """Compose a compact overview from the structured scrape — replaces shipping the raw
    profile JSON and raw website text to the drafter (huge token saving)."""
    lines: list[str] = []
    offering = profile.get("offering") or {}
    for prod in (offering.get("products") or [])[:4]:
        parts = [x for x in (prod.get("name"), prod.get("description")) if x] if isinstance(prod, dict) else []
        if parts:
            lines.append(f"- Product/Service: {' — '.join(parts)}")
    if offering.get("target_customer_hint"):
        lines.append(f"- Target customers: {offering['target_customer_hint']}")
    signals = profile.get("icp_signals") or {}
    if signals.get("industries_served"):
        lines.append(f"- Industries served: {', '.join(signals['industries_served'][:6])}")
    if signals.get("use_cases"):
        lines.append(f"- Use cases: {', '.join(signals['use_cases'][:6])}")
    return "\n".join(lines) or "Not available"


def _news_block(profile: dict) -> str:
    items = []
    for n in (profile.get("news") or [])[:3]:
        if isinstance(n, dict) and n.get("title"):
            date = f" ({str(n['date'])[:10]})" if n.get("date") else ""
            items.append(f"- {n['title']}{date}: {n.get('summary') or ''}".rstrip(": "))
    return "\n".join(items) or "None found"


def _initial_email_sections(
    lead: Lead, company: Company, icp: ICP, intelligence: CompanyIntelligence,
    sender_name: str, sender_company: str, variant_instruction: str, location: str,
) -> list[str]:
    """Curated inputs for the initial-email drafter: everything it needs, nothing more."""
    profile = company.enrichment_profile or {}
    person = _matched_person(lead, profile)
    # Pain points live on this run's qualification verdict, not the canonical company.
    qualification = next(
        (q for q in company.qualifications if q.lead_import_id == lead.lead_import_id), None
    )
    pains = (qualification.solvable_pain_points if qualification else None) or []
    sections = [
        f"TONE (from ICP): {icp.outreach_tone}",
        f"CAMPAIGN OBJECTIVE: {icp.campaign_objective}",
        f"ENGLISH VARIANT: {variant_instruction}",
        "",
        "SENDER:",
        f"- Name: {sender_name} (first name for the sign-off: {_sender_first_name(sender_name)})",
        f"- Company: {sender_company}",
        f"- Company overview: {intelligence.summary}",
        f"- Services: {[s.get('name') for s in (intelligence.services or [])][:5]}",
        f"- Value propositions: {intelligence.value_propositions}",
        "",
        "RECIPIENT (the lead — speak to this person):",
        f"- Name: {lead.full_name} (first name: {lead.first_name or lead.full_name.split()[0]})",
        f"- Title: {lead.title or 'unknown'}",
        f"- Seniority: {lead.seniority or 'unknown'}",
        *([f"- Time in current role: {lead.time_in_role}"] if lead.time_in_role else []),
        f"- Location: {location or 'unknown'}",
        "",
        "RECIPIENT COMPANY:",
        f"- Name: {company.name} ({company.industry or 'industry unknown'})",
        f"- Description: {company.description or 'N/A'}",
        f"- Overview from their website:\n{_company_overview(company, profile)}",
        "",
        f"SOLVABLE PAIN POINTS (pick the primary pain from these; each has its evidence):\n"
        f"{pains or 'None extracted'}",
        "",
        f"RECIPIENT COMPANY NEWS/SIGNALS (personalization opener when there is no person match):\n{_news_block(profile)}",
        "",
        "PERSON MATCH: "
        + (str(person) if person else "None — the recipient does not appear on the company website; use a company-level signal for the opener."),
    ]
    if not pains:
        sections += ["", f"ICP INTENT KEYWORDS (fallback for deriving the pain): {icp.target_keywords or []}"]
    return sections


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

    sender_name = (sender.full_name if sender else None) or "The team"
    sender_company = intelligence.company_name or "our company"
    variant_instruction, location = _english_variant(lead, company)
    system_prompt = _system_prompt_for(draft)
    is_initial_email = system_prompt is SYSTEM_PROMPT

    if is_initial_email and mode in _SLIM_REFINE_MODES and draft.body:
        # Slim prompt: these modes rewrite the CURRENT DRAFT without adding facts, so the
        # draft itself is the only fact source they need.
        instruction_label = "REGENERATE INSTRUCTION" if mode == "regenerate" else "REFINEMENT INSTRUCTION"
        instruction = REGENERATE_INSTRUCTION if mode == "regenerate" else REFINE_INSTRUCTIONS[mode]
        sections = [
            f"TONE (from ICP): {icp.outreach_tone}",
            f"ENGLISH VARIANT: {variant_instruction}",
            "",
            "SENDER:",
            f"- First name (for the sign-off): {_sender_first_name(sender_name)}",
            f"- Company: {sender_company}",
            "",
            "RECIPIENT:",
            f"- First name: {lead.first_name or lead.full_name.split()[0]}",
            f"- Company: {company.name}",
        ]
        if mode == "regenerate":
            sections += [
                "",
                "PREVIOUS DRAFTS OF THIS STEP (do not drift back to their phrasing):\n"
                f"{_previous_drafts_block(draft)}",
            ]
        sections += [
            "",
            f"CURRENT DRAFT (the one to {'rewrite' if mode == 'regenerate' else 'refine'}):",
            f"Subject: {draft.subject or ''}",
            draft.body or "",
            "",
            f"{instruction_label}: {instruction}",
        ]
        return _complete_draft(db, draft, "\n".join(sections), system_prompt, mode)

    if is_initial_email:
        # Curated inputs for the pain-hook template (initial + more_technical /
        # personalize_further, which need the full data to add facts from).
        sections = _initial_email_sections(
            lead, company, icp, intelligence, sender_name, sender_company, variant_instruction, location
        )
        if draft.history:
            sections += ["", f"PREVIOUS DRAFTS OF THIS STEP:\n{_previous_drafts_block(draft)}"]
        if is_refine:
            sections += [
                "",
                "CURRENT DRAFT (the one to refine):",
                f"Subject: {draft.subject or ''}",
                draft.body or "",
                "",
                f"REFINEMENT INSTRUCTION: {REFINE_INSTRUCTIONS[mode]}",
            ]
        return _complete_draft(db, draft, "\n".join(sections), system_prompt, mode)

    # Full prompt path only: crawl (or reuse cached) website content for personalization.
    enrichment = _enrich_company(company)
    if enrichment and not company.enrichment_content:
        company.enrichment_content = enrichment[:20000]
        db.commit()

    sections = [
        f"TONE (from ICP): {icp.outreach_tone}",
        f"CAMPAIGN OBJECTIVE: {icp.campaign_objective}",
        f"ENGLISH VARIANT: {variant_instruction}",
        "",
        "SENDER:",
        f"- Name: {sender_name}",
        f"- First name (for the sign-off): {_sender_first_name(sender_name)}",
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
        objective = _FOLLOW_UP_OBJECTIVES[draft.step_index]
        thread_pain = _thread_primary_pain(db, draft)
        header = [
            f"THIS IS {STEP_LABELS[draft.step_index].upper()} OF 3 IN THE SEQUENCE.",
            f"STEP OBJECTIVE: {objective}",
        ]
        if thread_pain:
            header.append(
                f"THREAD PRIMARY PAIN (continue THIS same pain — do NOT introduce a new one): {thread_pain}"
            )
        sections.insert(0, "\n".join(header))
    if is_refine:
        sections += [
            "",
            "CURRENT DRAFT (the one to refine):",
            f"Subject: {draft.subject or ''}",
            draft.body or "",
            "",
            f"REFINEMENT INSTRUCTION: {REFINE_INSTRUCTIONS[mode]}",
        ]
    return _complete_draft(db, draft, "\n".join(sections), system_prompt, mode)


def _complete_draft(db: Session, draft: EmailDraft, user_prompt: str, system_prompt: str, mode: str) -> EmailDraft:
    """Run the completion and write the result (or failure) onto the draft row."""
    try:
        # Regenerate/refine must produce a NEW draft, so bypass the response cache for
        # everything except the very first generation.
        temperature = _TEMPERATURE_BY_MODE.get(mode, 0.7)  # refine modes default to 0.7
        data, was_cached = cached_json_completion(
            system_prompt, user_prompt, skip_cache=mode != "initial", temperature=temperature
        )
        referenced = data.get("referenced_data") or []
        if isinstance(referenced, list):
            referenced = [str(r).strip() for r in referenced if str(r).strip()]
        else:
            referenced = [str(referenced)]
        primary_pain = str(data.get("primary_pain") or "").strip()
        if primary_pain:
            referenced.insert(0, f"Primary pain: {primary_pain}")
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
