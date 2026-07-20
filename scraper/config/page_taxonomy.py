"""
Keyword/synonym taxonomy used to score discovered links into page types.
This is the mechanism that replaces hardcoded routes: any URL or anchor
text is scored against these token sets regardless of the site's own
naming convention (e.g. "/products", "/what-we-offer", "/solutions" all
match "product").

Scope is intentionally narrow: only the page types downstream agents
consume (qualification fit, decision-makers, growth signals, outreach
hooks). Firmographics (industry, size, geography) arrive from the lead
CSV, so about/pricing/contact/faq/legal pages are never crawled.

Editable without touching pipeline code.
"""

from __future__ import annotations

PAGE_TYPE_KEYWORDS: dict[str, list[str]] = {
    "product": ["product", "products", "platform", "solution", "solutions", "features", "how-it-works", "services", "what-we-do"],
    "team": ["team", "leadership", "people", "founders", "management", "our-team"],
    "careers": ["careers", "jobs", "join-us", "hiring", "work-with-us"],
    "customers": ["customers", "case-studies", "case-study", "clients", "testimonials", "success-stories", "portfolio"],
    "news": ["news", "press", "newsroom", "media", "announcements", "updates", "blog"],
}

# Relevance weight per page type when ranking crawl budget allocation.
# "other" is 0.0 so unscoped pages never win crawl budget.
PAGE_TYPE_WEIGHT: dict[str, float] = {
    "product": 1.0,
    "news": 0.9,
    "team": 0.8,
    "customers": 0.7,
    "careers": 0.6,
    "other": 0.0,
    "home": 1.0,
}

# Hard cap on pages fetched per type; the total crawl is home + these.
PAGE_TYPE_MAX_PAGES: dict[str, int] = {
    "product": 2,
    "news": 2,
    "team": 1,
    "careers": 1,
    "customers": 1,
}

NEWS_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "funding": ["raises", "funding", "series a", "series b", "series c", "investment", "valuation"],
    "partnership": ["partners with", "partnership", "collaborat", "alliance"],
    "award": ["award", "recognized", "named", "winner", "ranked"],
    "product_update": ["launches", "release", "introducing", "new feature", "unveils"],
    "event": ["webinar", "conference", "summit", "attending", "booth"],
    "press_release": ["press release", "announces"],
}
