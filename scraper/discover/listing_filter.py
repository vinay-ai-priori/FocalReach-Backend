"""
Drops index/listing pages (e.g. "/news/", "/category/case-study/",
"/blog/page/2/") when the same crawl already found deeper detail pages
under that path — the listing page's content is just a redundant
preview/duplicate of pages we're already scraping in full, and it's the
main source of boilerplate noise (pagination controls, nav, teaser text).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from scraper.discover.link_scoring import ScoredLink

_JUNK_PATH_RE = re.compile(r"/(category|tag|page/\d+|author)(/|$)", re.IGNORECASE)


def _normalized_path(url: str) -> str:
    return urlparse(url).path.rstrip("/")


def filter_listing_pages(ranked: list[ScoredLink]) -> list[ScoredLink]:
    paths = [(_normalized_path(link.url), link) for link in ranked]

    kept: list[ScoredLink] = []
    for path, link in paths:
        if _JUNK_PATH_RE.search(path):
            continue

        is_prefix_of_another = any(
            other_path != path and other_path.startswith(path + "/") for other_path, _ in paths
        )
        if is_prefix_of_another and link.page_type != "other":
            continue

        kept.append(link)

    return kept
