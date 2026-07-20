from __future__ import annotations

from pydantic import BaseModel


class ScraperSettings(BaseModel):
    # Concurrency / latency knobs
    max_concurrent_requests: int = 20
    max_concurrent_browser_pages: int = 4
    # Caps how many full scrape_company_site() calls run at once against
    # a shared ScraperRuntime, so a burst of production calls can't starve
    # each other's connection pool / browser contexts.
    max_concurrent_scrapes: int = 5
    http_timeout_seconds: float = 8.0
    browser_timeout_seconds: float = 15.0
    global_budget_seconds: float = 120.0

    # Crawl scope. No page-count cap: every non-legal page discovery finds
    # is scraped. max_discovered_links is only a sanity ceiling against
    # pathological sites with thousands of URLs.
    max_discovered_links: int = 500
    max_news_items: int = 30
    news_lookback_days: int = 365

    # Content sufficiency thresholds (decide httpx result is "good enough")
    min_text_length_for_success: int = 200
    min_text_to_dom_ratio: float = 0.02

    # Caching of raw fetched HTML on disk. Off by default: production only
    # persists the LLM-structured profile (global_companies table), never raw
    # pages. Set a directory to opt in (e.g. local dev iteration on extraction).
    cache_ttl_seconds: int = 60 * 60 * 24  # 24h
    cache_dir: str | None = None

    # LLM enrichment
    enable_llm_enrichment: bool = True
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    # gpt-4o-mini has a 128k-token context; ~300k chars leaves headroom for
    # the system prompt and output. Raised from 40k so completeness isn't
    # bottlenecked by an arbitrary input cap.
    llm_max_input_chars: int = 300_000

    user_agent: str = (
        "FocalReachBot/1.0 (+https://focalreach.example; company research assistant)"
    )


DEFAULT_SETTINGS = ScraperSettings()
