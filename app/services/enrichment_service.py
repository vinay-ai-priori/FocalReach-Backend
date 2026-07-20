"""Company enrichment: deep-scrape the target company's website with the scraper pipeline.

Runs only for companies that passed the deterministic qualification gates (location +
employee size). The structured profile (offering, ICP signals, people, news, social
proof, tech signals) feeds downstream fit scoring and email personalization.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.logging import get_logger
from app.models.company import Company
from app.models.global_company import GlobalCompany
from app.services.website.url_validator import extract_domain
from scraper.config.settings import ScraperSettings
from scraper.pipeline import scrape_company_site
from scraper.runtime import ScraperRuntime
from scraper.schema.models import ScrapeResult

logger = get_logger(__name__)

# Wave-parallel scraping: one shared runtime (single httpx pool + single Chromium)
# serves QUALIFY_PARALLELISM concurrent site scrapes — parallelism without paying a
# browser process per company. max_discovered_links caps the crawl to the top-ranked
# pages (home, about, products, pricing, customers...) — without it, large sites blow
# the time budget fetching hundreds of long-tail pages and the scrape returns nothing.
SCRAPER_SETTINGS = ScraperSettings(
    max_concurrent_scrapes=app_settings.QUALIFY_PARALLELISM,
    max_discovered_links=15,
    max_concurrent_browser_pages=6,
    global_budget_seconds=120.0,
)

ENRICHMENT_CONTENT_MAX_CHARS = 20_000


async def _scrape_many(urls: list[str]) -> list[ScrapeResult | BaseException]:
    """Scrape up to QUALIFY_PARALLELISM sites concurrently against one shared runtime.

    Returns results in input order; a failed scrape yields its exception instead of
    aborting the whole wave.
    """
    async with ScraperRuntime(SCRAPER_SETTINGS) as runtime:
        return await asyncio.gather(
            *[
                scrape_company_site(url, runtime, SCRAPER_SETTINGS, openai_api_key=app_settings.OPENAI_API_KEY)
                for url in urls
            ],
            return_exceptions=True,
        )


def _profile_as_text(result: ScrapeResult) -> str:
    """Flatten the structured scrape into readable text for email personalization."""
    lines: list[str] = []
    offering = result.offering
    for product in offering.products:
        parts = [p for p in (product.name, product.description) if p]
        if parts:
            lines.append(f"Product/Service: {' — '.join(parts)}")
    if offering.key_features:
        lines.append(f"Key features: {', '.join(offering.key_features)}")
    if offering.integrations:
        lines.append(f"Integrations: {', '.join(offering.integrations)}")
    if offering.pricing_model_hint:
        lines.append(f"Pricing model: {offering.pricing_model_hint}")
    if offering.target_customer_hint:
        lines.append(f"Target customer: {offering.target_customer_hint}")

    signals = result.icp_signals
    if signals.industries_served:
        lines.append(f"Industries served: {', '.join(signals.industries_served)}")
    if signals.use_cases:
        lines.append(f"Use cases: {', '.join(signals.use_cases)}")
    if signals.customer_logos:
        lines.append(f"Customers: {', '.join(signals.customer_logos)}")
    for snippet in signals.case_study_snippets:
        if snippet.summary:
            lines.append(f"Case study ({snippet.customer or 'customer'}): {snippet.summary}")
    if signals.certifications_compliance:
        lines.append(f"Certifications/compliance: {', '.join(signals.certifications_compliance)}")

    for person in result.people:
        parts = [p for p in (person.name, person.title) if p]
        if parts:
            lines.append(f"Person: {', '.join(parts)}")
    for item in result.news:
        if item.title:
            date = f" ({item.date.date().isoformat()})" if item.date else ""
            lines.append(f"News{date}: {item.title}. {item.summary or ''}".strip())
    for quote in result.social_proof.testimonials:
        lines.append(f"Testimonial: {quote}")
    if result.social_proof.awards:
        lines.append(f"Awards: {', '.join(result.social_proof.awards)}")
    if result.tech_signals.detected_tools:
        lines.append(f"Technologies detected: {', '.join(result.tech_signals.detected_tools)}")

    return "\n".join(lines)[:ENRICHMENT_CONTENT_MAX_CHARS]


def _has_signal(result: ScrapeResult) -> bool:
    """A scrape that found no pages (unreachable site / budget timeout) is a failure."""
    return result.stats.pages_scraped > 0


def _resolve_domain(company: Company) -> str | None:
    return company.domain or (extract_domain(company.website) if company.website else None)


def _fresh_global_row(db: Session, domain: str | None) -> GlobalCompany | None:
    """The cross-campaign cache row for this domain, if it's still within its TTL."""
    if not domain:
        return None
    row = db.scalar(select(GlobalCompany).where(GlobalCompany.domain == domain))
    if row and row.enrichment_profile and row.valid_till and datetime.now(timezone.utc) <= row.valid_till:
        return row
    return None


def _upsert_global_row(db: Session, domain: str, company: Company) -> None:
    """Write the fresh enrichment to the global cache — update in place, never duplicate."""
    now = datetime.now(timezone.utc)
    row = db.scalar(select(GlobalCompany).where(GlobalCompany.domain == domain))
    if row is None:
        row = GlobalCompany(domain=domain)
        db.add(row)
    row.name = company.name
    row.website = company.website
    row.enrichment_profile = company.enrichment_profile
    row.enrichment_content = company.enrichment_content
    row.enriched_at = now
    row.valid_till = now + timedelta(days=app_settings.ENRICHMENT_TTL_DAYS)


def _apply_scrape(db: Session, company: Company, domain: str | None, result: ScrapeResult | BaseException) -> None:
    """Write one scrape outcome onto the company row (and the global cache on success)."""
    if isinstance(result, BaseException):
        logger.warning("Enrichment scrape failed for %s: %s", company.website, result)
        company.enriched_at_status = "failed"
        return
    if not _has_signal(result):
        logger.warning(
            "Enrichment scrape returned no pages for %s (truncated_by_budget=%s)",
            company.website,
            result.stats.truncated_by_budget,
        )
        company.enriched_at_status = "failed"
        return

    company.enrichment_profile = result.model_dump(mode="json", exclude={"stats"})
    if not company.enrichment_content:
        company.enrichment_content = _profile_as_text(result)
    company.enriched_at_status = "enriched"
    if domain:
        _upsert_global_row(db, domain, company)

    usage = result.stats.llm_usage
    logger.info(
        "Enriched %s: %d pages in %dms (llm tokens=%s)",
        result.domain,
        result.stats.pages_scraped,
        result.stats.duration_ms,
        usage.total_tokens if usage else "n/a",
    )


def enrich_companies(db: Session, companies: list[Company]) -> None:
    """Enrich a wave of companies: cache-first, then one parallel scrape of the misses.

    Per company, in order: already-enriched rows are untouched; no website -> marked;
    a fresh global_companies row (cross-campaign cache) is copied at zero cost; the
    remainder are scraped concurrently against a shared runtime (one httpx pool + one
    Chromium). Individual failures are non-fatal — those companies continue through
    qualification with CSV data only. Commits once for the whole wave.
    """
    to_scrape: list[tuple[Company, str | None]] = []
    for company in companies:
        if company.enrichment_profile:
            continue
        if not company.website:
            company.enriched_at_status = "no_website"
            continue
        domain = _resolve_domain(company)
        cached = _fresh_global_row(db, domain)
        if cached:
            company.enrichment_profile = cached.enrichment_profile
            if not company.enrichment_content:
                company.enrichment_content = cached.enrichment_content
            company.enriched_at_status = "enriched"
            logger.info("Enrichment cache hit for %s (valid till %s)", domain, cached.valid_till)
            continue
        to_scrape.append((company, domain))

    if to_scrape:
        try:
            results = asyncio.run(_scrape_many([company.website for company, _ in to_scrape]))
        except Exception as exc:  # runtime startup/teardown failure hits the whole wave
            logger.warning("Enrichment wave failed: %s", exc)
            results = [exc] * len(to_scrape)
        for (company, domain), result in zip(to_scrape, results):
            _apply_scrape(db, company, domain, result)

    db.commit()
