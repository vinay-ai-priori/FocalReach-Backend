"""Company enrichment: crawl the target company's website and structure it with AI.

Runs only for companies that passed the deterministic qualification gates (location +
employee size). The structured profile makes downstream keyword tallying (company fit
scoring) and email personalization straightforward, instead of re-parsing raw page text.
"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.company import Company
from app.services.ai.openai_client import cached_json_completion
from app.services.website.cache import get_cached_content, set_cached_content
from app.services.website.crawler import crawl_website
from app.services.website.url_validator import normalize_url

logger = get_logger(__name__)

ENRICHMENT_SYSTEM_PROMPT = """You are a B2B research analyst. You are given raw text scraped from a company's website.
Extract a structured, factual profile of the company. Do not invent facts — only use what the text supports.
Return ONLY a JSON object with exactly these keys:
{
  "summary": string (2-3 sentences on what the company does),
  "products_services": [string] (their main offerings),
  "industries_served": [string] (industries/verticals they serve or belong to),
  "technologies": [string] (technologies, platforms, or tools mentioned),
  "pain_points_addressed": [string] (customer problems they claim to solve),
  "keywords": [string] (10-20 salient terms/phrases that characterize the business)
}
If the text is too thin to support a field, return an empty list (or empty string for summary)."""


def _crawl_company_site(company: Company) -> str:
    """Fetch the company's website text, using the shared per-domain Redis cache."""
    if company.enrichment_content:
        return company.enrichment_content
    if not company.website:
        return ""
    domain = company.domain or ""
    cached = get_cached_content(domain) if domain else None
    if cached:
        return cached.get("content", "")
    result = crawl_website(normalize_url(company.website))
    if domain:
        set_cached_content(domain, {"content": result.content[:20000]})
    return result.content


def enrich_company(db: Session, company: Company) -> dict | None:
    """Crawl + structure the company website. Returns the profile, or None on failure.

    Failure is non-fatal: the company continues through qualification with CSV data only.
    """
    if company.enrichment_profile:
        return company.enrichment_profile

    try:
        content = _crawl_company_site(company)
    except Exception as exc:
        logger.warning("Enrichment crawl failed for %s: %s", company.website, exc)
        content = ""

    if not content.strip():
        company.enriched_at_status = "failed" if company.website else "no_website"
        db.commit()
        return None

    if not company.enrichment_content:
        company.enrichment_content = content[:20000]

    try:
        profile, _ = cached_json_completion(
            ENRICHMENT_SYSTEM_PROMPT,
            f"Company: {company.name}\nWebsite: {company.website}\n\nWEBSITE TEXT:\n{content[:12000]}",
        )
    except Exception as exc:
        logger.warning("Enrichment AI structuring failed for %s: %s", company.name, exc)
        company.enriched_at_status = "failed"
        db.commit()
        return None

    company.enrichment_profile = profile
    company.enriched_at_status = "enriched"
    db.commit()
    return profile
