"""
Merges per-page ProcessedPage results into the final ScrapeResult.
Deterministic fields (tech_signals) are filled here directly. Rich
semantic fields (products, ICP signals, people, case studies, social
proof) are intentionally left for the downstream GPT-4o-mini step, which
reads the bucketed raw page text produced during this pass.
"""

from __future__ import annotations

from scraper.extract.page_processor import ProcessedPage
from scraper.schema.models import ScrapeResult, TechSignals


def aggregate(
    domain: str,
    processed_pages: list[ProcessedPage],
    news_items: list,
    scraped_at,
) -> ScrapeResult:
    result = ScrapeResult(domain=domain, scraped_at=scraped_at)

    tools: set[str] = set()
    for page in processed_pages:
        tools.update(page.tech_tools)

    result.tech_signals = TechSignals(detected_tools=sorted(tools))
    result.news = news_items

    return result
