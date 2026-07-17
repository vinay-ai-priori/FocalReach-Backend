from __future__ import annotations

import asyncio

from playwright.async_api import Browser

from scraper.config.settings import ScraperSettings
from scraper.fetch.models import FetchResult


async def _fetch_one_page(
    browser: Browser,
    url: str,
    semaphore: asyncio.Semaphore,
    settings: ScraperSettings,
) -> FetchResult:
    async with semaphore:
        context = await browser.new_context(user_agent=settings.user_agent)
        page = await context.new_page()
        try:
            await page.goto(
                url,
                timeout=settings.browser_timeout_seconds * 1000,
                wait_until="domcontentloaded",
            )
            # Give client-side rendering a brief moment without hard-blocking.
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            html = await page.content()
            return FetchResult(url=url, html=html, status_code=200, fetched_via="playwright")
        except Exception as exc:
            return FetchResult(url=url, html=None, status_code=None, fetched_via="playwright", error=str(exc))
        finally:
            await context.close()


async def fetch_many_via_browser(
    browser: Browser,
    urls: list[str],
    settings: ScraperSettings,
) -> list[FetchResult]:
    if not urls:
        return []

    semaphore = asyncio.Semaphore(settings.max_concurrent_browser_pages)
    tasks = [_fetch_one_page(browser, url, semaphore, settings) for url in urls]
    return await asyncio.gather(*tasks)
