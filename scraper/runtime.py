"""
Process-lifetime resources shared across scrape calls when this tool runs
as a long-lived service/worker rather than a fresh CLI process per call:

- one httpx.AsyncClient (keep-alive connection pool, avoids repeat TLS
  handshakes per call)
- one lazily-launched Playwright Chromium browser (avoids paying browser
  startup/teardown cost on every JS-fallback)
- a semaphore capping how many full site-scrapes run concurrently, so a
  burst of calls doesn't starve individual requests of connections/CPU
- a shared on-disk cache instance

Usage:
    async with ScraperRuntime(settings) as runtime:
        result = await scrape_company_site(url, settings, runtime=runtime)
        result2 = await scrape_company_site(url2, settings, runtime=runtime)
"""

from __future__ import annotations

import asyncio

import httpx
from playwright.async_api import Browser, Playwright, async_playwright

from scraper.config.settings import ScraperSettings
from scraper.utils.cache import DiskCache


class ScraperRuntime:
    def __init__(self, settings: ScraperSettings):
        self.settings = settings
        self.cache = DiskCache(settings)
        self.scrape_semaphore = asyncio.Semaphore(settings.max_concurrent_scrapes)

        self._client: httpx.AsyncClient | None = None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = asyncio.Lock()

    async def __aenter__(self) -> "ScraperRuntime":
        limits = httpx.Limits(
            max_connections=self.settings.max_concurrent_requests * 4,
            max_keepalive_connections=self.settings.max_concurrent_requests * 2,
        )
        self._client = httpx.AsyncClient(
            http2=True,
            limits=limits,
            headers={"User-Agent": self.settings.user_agent},
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        if self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ScraperRuntime must be entered with 'async with' before use")
        return self._client

    async def get_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        async with self._browser_lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser
