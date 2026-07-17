from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from scraper.extract.main_content import extract_main_text
from scraper.extract.meta_extractor import extract_page_title, extract_tech_signals
from scraper.schema.models import RawPage


@dataclass
class ProcessedPage:
    raw_page: RawPage
    title: str | None = None
    tech_tools: list[str] = field(default_factory=list)


def process_page(url: str, html: str, page_type: str, fetched_via: str) -> ProcessedPage:
    text = extract_main_text(html, url)

    raw_page = RawPage(
        url=url,
        page_type=page_type,
        extracted_text=text,
        fetched_via=fetched_via,
        extracted_at=datetime.now(timezone.utc),
    )

    return ProcessedPage(
        raw_page=raw_page,
        title=extract_page_title(html),
        tech_tools=extract_tech_signals(html),
    )
