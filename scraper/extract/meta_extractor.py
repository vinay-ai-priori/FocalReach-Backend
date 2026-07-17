"""
Cheap, deterministic extraction of structured signals directly from HTML
that don't need an LLM: page titles and third-party tool script detection.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

_KNOWN_TOOLS = {
    "hubspot": "HubSpot",
    "hs-scripts": "HubSpot",
    "segment.com": "Segment",
    "googletagmanager": "Google Tag Manager",
    "google-analytics": "Google Analytics",
    "intercom": "Intercom",
    "drift.com": "Drift",
    "marketo": "Marketo",
    "mixpanel": "Mixpanel",
    "salesforce": "Salesforce",
    "hotjar": "Hotjar",
    "segment.io": "Segment",
}


def extract_page_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")

    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return og_title["content"].strip()

    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)

    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)

    return None


def extract_tech_signals(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for script in soup.find_all("script", src=True):
        src = script["src"].lower()
        for token, label in _KNOWN_TOOLS.items():
            if token in src:
                found.add(label)
    return sorted(found)
