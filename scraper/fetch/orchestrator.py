from __future__ import annotations

from scraper.config.settings import ScraperSettings
from scraper.fetch.httpx_client import fetch_many
from scraper.fetch.models import FetchResult
from scraper.fetch.playwright_client import fetch_many_via_browser
from scraper.fetch.sufficiency import is_content_sufficient
from scraper.runtime import ScraperRuntime


async def fetch_urls(
    urls: list[str],
    settings: ScraperSettings,
    runtime: ScraperRuntime,
) -> tuple[list[FetchResult], int]:
    """
    Tiered fetch: httpx first for all URLs (cache-checked, shared client),
    then Playwright only for the subset that came back empty/insufficient
    (JS-rendered shells), using the runtime's shared browser instance.

    Returns (results, fallback_used_count).
    """
    httpx_results = await fetch_many(runtime.client, urls, settings, runtime.cache)

    needs_fallback: list[str] = []
    result_by_url: dict[str, FetchResult] = {}

    for res in httpx_results:
        result_by_url[res.url] = res
        if not res.ok or not is_content_sufficient(res.html, settings):
            needs_fallback.append(res.url)

    if needs_fallback:
        browser = await runtime.get_browser()
        browser_results = await fetch_many_via_browser(browser, needs_fallback, settings)
        for res in browser_results:
            # Prefer the browser result only if it actually improved things.
            if res.ok and is_content_sufficient(res.html, settings):
                result_by_url[res.url] = res
            elif res.ok and result_by_url[res.url].html is None:
                result_by_url[res.url] = res

    ordered = [result_by_url[u] for u in urls if u in result_by_url]
    return ordered, len(needs_fallback)
