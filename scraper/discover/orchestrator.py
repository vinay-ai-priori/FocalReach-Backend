from __future__ import annotations

import tldextract

from scraper.config.settings import ScraperSettings
from scraper.discover.link_extractor import extract_internal_links, find_feed_url
from scraper.discover.link_scoring import ScoredLink, rank_links
from scraper.discover.listing_filter import filter_listing_pages


def root_domain_of(url: str) -> str:
    ext = tldextract.extract(url)
    return ".".join(part for part in [ext.domain, ext.suffix] if part)


def discover_pages(
    base_url: str,
    homepage_html: str,
    sitemap_urls: list[str],
    settings: ScraperSettings,
) -> tuple[list[ScoredLink], str | None]:
    """
    Combines sitemap URLs (fetched by the caller, typically concurrently
    with the homepage) + links found on the homepage, scores them via the
    taxonomy, and returns a ranked candidate list plus an optional
    news/blog feed URL if discoverable from the homepage <head>.
    """
    root_domain = root_domain_of(base_url)

    homepage_links = extract_internal_links(homepage_html, base_url, root_domain)
    feed_url = find_feed_url(homepage_html, base_url)

    candidates: list[tuple[str, str]] = [(u, "") for u in sitemap_urls]
    candidates.extend(homepage_links)

    ranked = rank_links(candidates, max_links=settings.max_discovered_links)
    ranked = filter_listing_pages(ranked)
    return ranked, feed_url
