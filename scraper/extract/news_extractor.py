from __future__ import annotations

from datetime import datetime, timedelta, timezone

import feedparser

from scraper.config.page_taxonomy import NEWS_CATEGORY_KEYWORDS
from scraper.config.settings import ScraperSettings
from scraper.schema.models import NewsItem


def categorize_news(title: str, summary: str) -> str:
    combined = f"{title} {summary}".lower()
    for category, keywords in NEWS_CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return category
    return "blog"


def parse_feed(feed_content: str, settings: ScraperSettings) -> list[NewsItem]:
    parsed = feedparser.parse(feed_content)
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.news_lookback_days)

    items: list[NewsItem] = []
    for entry in parsed.entries[: settings.max_news_items * 2]:
        title = entry.get("title", "")
        summary = entry.get("summary", "") or entry.get("description", "")
        link = entry.get("link")

        published_dt: datetime | None = None
        if getattr(entry, "published_parsed", None):
            published_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

        if published_dt and published_dt < cutoff:
            continue

        items.append(
            NewsItem(
                title=title or None,
                date=published_dt,
                summary=summary or None,
                url=link,
                category=categorize_news(title, summary),
            )
        )

    items.sort(key=lambda i: i.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[: settings.max_news_items]


def news_items_from_pages(pages: list[tuple[str, str, str | None]], settings: ScraperSettings) -> list[NewsItem]:
    """
    Fallback when no RSS/Atom feed is discoverable: treat each scraped
    news-type page as one news item, using the page's own <title>/<h1>
    (not a guess from body text) and its full extracted text.
    pages: list of (url, text, title)
    """
    items: list[NewsItem] = []
    for url, text, title in pages[: settings.max_news_items]:
        items.append(
            NewsItem(
                title=title,
                date=None,
                summary=text or None,
                url=url,
                category=categorize_news(title or "", text or ""),
            )
        )
    return items
