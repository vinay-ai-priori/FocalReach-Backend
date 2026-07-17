"""
Scores URLs/anchor text against the page-type taxonomy without any
hardcoded routes. Works by tokenizing the URL path and anchor text and
matching against synonym keyword sets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from scraper.config.page_taxonomy import PAGE_TYPE_KEYWORDS, PAGE_TYPE_WEIGHT

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _tokenize_path(url: str) -> set[str]:
    path = urlparse(url).path
    # split on separators so "case-studies" -> {"case", "studies", "case-studies"-ish}
    raw = re.split(r"[/_\-]+", path.lower())
    tokens = {t for t in raw if t}
    tokens |= _tokenize(path)
    return tokens


@dataclass
class ScoredLink:
    url: str
    page_type: str
    score: float
    anchor_text: str = ""


def classify_link(url: str, anchor_text: str = "") -> tuple[str, float]:
    path_tokens = _tokenize_path(url)
    anchor_tokens = _tokenize(anchor_text)

    best_type = "other"
    best_score = 0.0

    for page_type, keywords in PAGE_TYPE_KEYWORDS.items():
        score = 0.0
        for kw in keywords:
            kw_tokens = set(re.split(r"[\s\-]+", kw))
            if kw_tokens & path_tokens:
                score += 1.5
            if kw_tokens & anchor_tokens:
                score += 1.0
            # substring match as a softer signal (handles concatenated slugs)
            if kw.replace("-", "") in url.lower().replace("-", ""):
                score += 0.5

        if score > best_score:
            best_score = score
            best_type = page_type

    weight = PAGE_TYPE_WEIGHT.get(best_type, PAGE_TYPE_WEIGHT["other"])
    return best_type, best_score * weight


def rank_links(links: list[tuple[str, str]], max_links: int) -> list[ScoredLink]:
    """
    links: list of (url, anchor_text)
    Returns scored+sorted, deduplicated by url, homepage-agnostic ranking.
    """
    seen: dict[str, ScoredLink] = {}
    for url, anchor_text in links:
        page_type, score = classify_link(url, anchor_text)
        existing = seen.get(url)
        if existing is None or score > existing.score:
            seen[url] = ScoredLink(url=url, page_type=page_type, score=score, anchor_text=anchor_text)

    ranked = sorted(seen.values(), key=lambda s: s.score, reverse=True)
    return ranked[:max_links]
