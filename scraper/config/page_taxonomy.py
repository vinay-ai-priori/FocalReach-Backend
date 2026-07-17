"""
Keyword/synonym taxonomy used to score discovered links into page types.
This is the mechanism that replaces hardcoded routes: any URL or anchor
text is scored against these token sets regardless of the site's own
naming convention (e.g. "/company", "/who-we-are", "/about-us" all match "about").

Editable without touching pipeline code.
"""

from __future__ import annotations

PAGE_TYPE_KEYWORDS: dict[str, list[str]] = {
    "about": ["about", "who-we-are", "our-story", "company", "mission", "vision"],
    "product": ["product", "products", "platform", "solution", "solutions", "features", "how-it-works"],
    "pricing": ["pricing", "plans", "plan", "cost", "quote"],
    "team": ["team", "leadership", "people", "founders", "management", "our-team"],
    "careers": ["careers", "jobs", "join-us", "hiring", "work-with-us"],
    "customers": ["customers", "case-studies", "case-study", "clients", "testimonials", "success-stories", "portfolio"],
    "news": ["news", "press", "newsroom", "media", "announcements", "updates", "blog"],
    "contact": ["contact", "contact-us", "support", "get-in-touch"],
    "faq": ["faq", "faqs", "help"],
    "legal": ["privacy", "terms", "legal", "cookie"],
}

# Relevance weight per page type when ranking crawl budget allocation
PAGE_TYPE_WEIGHT: dict[str, float] = {
    "about": 1.0,
    "product": 1.0,
    "pricing": 0.7,
    "team": 0.8,
    "customers": 0.9,
    "news": 0.9,
    "contact": 0.6,
    "careers": 0.5,
    "faq": 0.3,
    "legal": 0.05,
    "other": 0.4,
    "home": 1.0,
}

NEWS_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "funding": ["raises", "funding", "series a", "series b", "series c", "investment", "valuation"],
    "partnership": ["partners with", "partnership", "collaborat", "alliance"],
    "award": ["award", "recognized", "named", "winner", "ranked"],
    "product_update": ["launches", "release", "introducing", "new feature", "unveils"],
    "event": ["webinar", "conference", "summit", "attending", "booth"],
    "press_release": ["press release", "announces"],
}
