from __future__ import annotations

import httpx
from lxml import etree

from scraper.config.settings import ScraperSettings


async def get_sitemap_urls(base_url: str, client: httpx.AsyncClient, settings: ScraperSettings) -> list[str]:
    """
    Reads robots.txt for Sitemap: directives, falls back to /sitemap.xml.
    Returns page URLs (recurses one level into sitemap indexes).
    Uses the shared client, independent of the homepage fetch, so callers
    can run this concurrently with fetching the homepage.
    """
    sitemap_locations: list[str] = []

    try:
        robots_resp = await client.get(f"{base_url}/robots.txt", timeout=settings.http_timeout_seconds)
        if robots_resp.status_code == 200:
            for line in robots_resp.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_locations.append(line.split(":", 1)[1].strip())
    except httpx.HTTPError:
        pass

    if not sitemap_locations:
        sitemap_locations.append(f"{base_url}/sitemap.xml")

    urls: list[str] = []
    for sitemap_url in sitemap_locations[:3]:
        urls.extend(await _parse_sitemap(client, sitemap_url, settings, depth=0))

    return urls


async def _parse_sitemap(
    client: httpx.AsyncClient, sitemap_url: str, settings: ScraperSettings, depth: int
) -> list[str]:
    if depth > 1:
        return []
    try:
        resp = await client.get(sitemap_url, timeout=settings.http_timeout_seconds)
        if resp.status_code != 200:
            return []
        root = etree.fromstring(resp.content)
    except Exception:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []

    # Sitemap index -> recurse
    sub_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
    if sub_sitemaps:
        for loc in sub_sitemaps[:10]:
            if loc.text:
                urls.extend(await _parse_sitemap(client, loc.text.strip(), settings, depth + 1))
        return urls

    for loc in root.findall(".//sm:url/sm:loc", ns):
        if loc.text:
            urls.append(loc.text.strip())

    return urls
