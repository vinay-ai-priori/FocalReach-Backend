from __future__ import annotations

import asyncio

import httpx

from scraper.config.settings import ScraperSettings
from scraper.fetch.models import FetchResult
from scraper.utils.cache import DiskCache


async def fetch_one(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    settings: ScraperSettings,
    cache: DiskCache | None = None,
) -> FetchResult:
    if cache is not None:
        cached_html = cache.get(url)
        if cached_html is not None:
            return FetchResult(url=url, html=cached_html, status_code=200, fetched_via="cache")

    async with semaphore:
        try:
            resp = await client.get(
                url,
                timeout=settings.http_timeout_seconds,
                follow_redirects=True,
            )
            if resp.status_code >= 400:
                return FetchResult(
                    url=url,
                    html=None,
                    status_code=resp.status_code,
                    fetched_via="httpx",
                    error=f"http_{resp.status_code}",
                )
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "xml" not in content_type:
                return FetchResult(
                    url=url,
                    html=None,
                    status_code=resp.status_code,
                    fetched_via="httpx",
                    error="non_html_content_type",
                )
            if cache is not None:
                cache.set(url, resp.text)
            return FetchResult(
                url=url,
                html=resp.text,
                status_code=resp.status_code,
                fetched_via="httpx",
            )
        except httpx.TimeoutException:
            return FetchResult(url=url, html=None, status_code=None, fetched_via="httpx", error="timeout")
        except httpx.HTTPError as exc:
            return FetchResult(url=url, html=None, status_code=None, fetched_via="httpx", error=str(exc))


async def fetch_many(
    client: httpx.AsyncClient,
    urls: list[str],
    settings: ScraperSettings,
    cache: DiskCache | None = None,
) -> list[FetchResult]:
    semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
    tasks = [fetch_one(client, url, semaphore, settings, cache) for url in urls]
    return await asyncio.gather(*tasks)
