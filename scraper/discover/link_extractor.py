from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def extract_internal_links(html: str, page_url: str, root_domain: str) -> list[tuple[str, str]]:
    """Returns list of (absolute_url, anchor_text) for same-domain links."""
    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc and root_domain not in parsed.netloc:
            continue
        # Strip fragment; keep query only if meaningful (rare for marketing sites)
        clean = parsed._replace(fragment="").geturl()
        anchor_text = a.get_text(strip=True) or a.get("title", "") or ""
        links.append((clean, anchor_text))

    return links


def find_feed_url(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel", [])).lower()
        type_attr = (link.get("type") or "").lower()
        if "alternate" in rel and ("rss" in type_attr or "atom" in type_attr):
            href = link.get("href")
            if href:
                return urljoin(page_url, href)
    return None
