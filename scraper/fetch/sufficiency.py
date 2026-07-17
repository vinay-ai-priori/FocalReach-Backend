"""
Decides whether an httpx-fetched HTML payload has "enough" real content,
or whether the page is a JS-rendered shell that needs a Playwright fallback.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from scraper.config.settings import ScraperSettings


def is_content_sufficient(html: str | None, settings: ScraperSettings) -> bool:
    if not html:
        return False

    soup = BeautifulSoup(html, "lxml")

    # Strip script/style/noscript before measuring text
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text_len = len(text)

    if text_len < settings.min_text_length_for_success:
        return False

    dom_len = len(html)
    if dom_len == 0:
        return False

    ratio = text_len / dom_len
    return ratio >= settings.min_text_to_dom_ratio
