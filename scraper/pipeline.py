from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from scraper.config.settings import DEFAULT_SETTINGS, ScraperSettings
from scraper.discover import ScoredLink, discover_pages, get_sitemap_urls, root_domain_of
from scraper.extract import aggregate, news_items_from_pages, parse_feed, process_page
from scraper.fetch import fetch_urls
from scraper.llm import enrich_with_llm, merge_enrichment
from scraper.runtime import ScraperRuntime
from scraper.schema.models import RawPage, ScrapeResult, ScrapeStats


def _normalize_base_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url.rstrip("/")


def _select_pages(ranked: list[ScoredLink]) -> list[ScoredLink]:
    """
    Selects every discovered page except pure-noise types (legal/privacy/
    terms), which carry no ICP or email-drafting value. No numeric cap:
    completeness takes priority over trimming the crawl here.
    """
    return [link for link in ranked if link.page_type != "legal"]


async def scrape_company_site(
    url: str,
    runtime: ScraperRuntime,
    settings: ScraperSettings = DEFAULT_SETTINGS,
    openai_api_key: str | None = None,
) -> ScrapeResult:
    start = time.monotonic()
    base_url = _normalize_base_url(url)
    domain = root_domain_of(base_url)

    async def _run() -> ScrapeResult:
        timings: dict[str, int] = {}

        # Homepage fetch and sitemap/robots discovery are independent of
        # each other, so run them concurrently instead of sequentially.
        t0 = time.monotonic()
        (home_results, home_fallback_count), sitemap_urls = await asyncio.gather(
            fetch_urls([base_url], settings, runtime),
            get_sitemap_urls(base_url, runtime.client, settings),
        )
        timings["fetch_homepage_and_sitemap_ms"] = int((time.monotonic() - t0) * 1000)

        home = home_results[0]
        if not home.ok:
            return ScrapeResult(
                domain=domain,
                scraped_at=datetime.now(timezone.utc),
                stats=ScrapeStats(
                    pages_found=0,
                    pages_scraped=0,
                    fallback_used_count=home_fallback_count,
                    stage_timings_ms=timings,
                ),
            )

        t0 = time.monotonic()
        ranked, feed_url = discover_pages(base_url, home.html, sitemap_urls, settings)
        selected = _select_pages(ranked)
        timings["discovery_ms"] = int((time.monotonic() - t0) * 1000)

        urls_to_fetch = [base_url] + [s.url for s in selected if s.url != base_url]
        page_type_by_url = {base_url: "home"}
        page_type_by_url.update({s.url: s.page_type for s in selected})

        t0 = time.monotonic()
        fetch_results, fallback_count = await fetch_urls(urls_to_fetch, settings, runtime)
        fallback_count += home_fallback_count
        timings["fetch_pages_ms"] = int((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
        ok_results = [res for res in fetch_results if res.ok]
        processed = await asyncio.gather(
            *[
                asyncio.to_thread(
                    process_page,
                    res.url,
                    res.html,
                    page_type_by_url.get(res.url, "other"),
                    res.fetched_via,
                )
                for res in ok_results
            ]
        )
        news_pages: list[tuple[str, str, str | None]] = [
            (p.raw_page.url, p.raw_page.extracted_text, p.title) for p in processed if p.raw_page.page_type == "news"
        ]
        timings["extraction_ms"] = int((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
        news_items = []
        if feed_url:
            feed_results, _ = await fetch_urls([feed_url], settings, runtime)
            if feed_results and feed_results[0].ok:
                news_items = parse_feed(feed_results[0].html, settings)
        if not news_items and news_pages:
            news_items = news_items_from_pages(news_pages, settings)
        timings["news_ms"] = int((time.monotonic() - t0) * 1000)

        result = aggregate(domain, processed, news_items, datetime.now(timezone.utc))

        llm_usage = None
        if settings.enable_llm_enrichment and openai_api_key:
            raw_pages: list[RawPage] = [p.raw_page for p in processed]
            t0 = time.monotonic()
            enrichment, llm_usage = await enrich_with_llm(domain, raw_pages, settings, openai_api_key)
            result = merge_enrichment(result, enrichment)
            timings["llm_enrichment_ms"] = int((time.monotonic() - t0) * 1000)

        result.stats = ScrapeStats(
            pages_found=len(ranked),
            pages_scraped=len(processed),
            fallback_used_count=fallback_count,
            duration_ms=int((time.monotonic() - start) * 1000),
            stage_timings_ms=timings,
            llm_usage=llm_usage,
        )
        return result

    async with runtime.scrape_semaphore:
        try:
            return await asyncio.wait_for(_run(), timeout=settings.global_budget_seconds)
        except asyncio.TimeoutError:
            return ScrapeResult(
                domain=domain,
                scraped_at=datetime.now(timezone.utc),
                stats=ScrapeStats(
                    duration_ms=int((time.monotonic() - start) * 1000),
                    truncated_by_budget=True,
                ),
            )
