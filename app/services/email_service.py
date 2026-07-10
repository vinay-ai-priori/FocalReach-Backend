"""Email drafting: crawls the target company site (cached per domain), then uses AI to
draft a personalized first-touch email combining the user's company intelligence,
ICP tone, and target company/lead context."""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company import Company
from app.models.company_intelligence import CompanyIntelligence
from app.models.email_draft import DraftStatus, EmailDraft
from app.models.icp import ICP
from app.models.lead import Lead
from app.services.ai.openai_client import cached_json_completion
from app.services.website.cache import get_cached_content, set_cached_content
from app.services.website.crawler import crawl_website
from app.services.website.url_validator import normalize_url

logger = get_logger(__name__)

SYSTEM_PROMPT = """You write concise, personalized B2B cold outreach emails.
Rules:
- 90-130 words max. No fluff, no "I hope this finds you well".
- Reference one concrete, specific detail about the recipient's company.
- Explain the sender's value in terms of the recipient's likely priorities.
- End with a soft call to action inviting a short discovery call at the provided booking link.
- Match the requested tone exactly.
Return ONLY a JSON object: {"subject": string, "body": string, "personalization_notes": string}
The body must be plain text with real line breaks, greeting the recipient by first name."""


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


def generate_email_draft(
    db: Session, draft: EmailDraft, lead: Lead, company: Company, icp: ICP, intelligence: CompanyIntelligence
) -> EmailDraft:
    draft.status = DraftStatus.GENERATING
    db.commit()

    enrichment = _enrich_company(company)
    if enrichment and not company.enrichment_content:
        company.enrichment_content = enrichment[:20000]
        db.commit()

    user_prompt = (
        f"TONE: {icp.outreach_tone}\n"
        f"CAMPAIGN OBJECTIVE: {icp.campaign_objective}\n\n"
        f"SENDER COMPANY:\n"
        f"- Name: {intelligence.company_name}\n"
        f"- What they do: {intelligence.summary}\n"
        f"- Key services: {[s.get('name') for s in (intelligence.services or [])][:5]}\n"
        f"- Value propositions: {intelligence.value_propositions}\n\n"
        f"RECIPIENT:\n"
        f"- Name: {lead.full_name} (first name: {lead.first_name or lead.full_name.split()[0]})\n"
        f"- Title: {lead.title}\n"
        f"- Company: {company.name} ({company.industry or 'industry unknown'}, "
        f"{company.employee_count or company.employee_range or 'size unknown'} employees, "
        f"{company.country or 'location unknown'})\n"
        f"- Company description: {company.description or 'N/A'}\n"
        f"- Structured profile from their website: {company.enrichment_profile or 'Not available'}\n\n"
        f"RECIPIENT COMPANY WEBSITE CONTENT (for personalization):\n{enrichment[:8000] or 'Not available'}\n\n"
        f"BOOKING LINK: {settings.CALCOM_BOOKING_URL}"
    )

    try:
        data, was_cached = cached_json_completion(SYSTEM_PROMPT, user_prompt)
        draft.subject = data.get("subject")
        draft.body = data.get("body")
        draft.personalization_notes = data.get("personalization_notes")
        draft.booking_link = settings.CALCOM_BOOKING_URL
        draft.ai_model = settings.OPENAI_MODEL
        draft.ai_cached = was_cached
        draft.status = DraftStatus.READY
    except Exception as exc:
        draft.status = DraftStatus.FAILED
        draft.error_message = str(exc)[:1000]
        logger.exception("Email draft %s failed", draft.id)
    db.commit()
    db.refresh(draft)
    return draft
