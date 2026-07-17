from __future__ import annotations

import trafilatura

from scraper.extract.text_cleaner import clean_text


def extract_main_text(html: str, url: str) -> str:
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    return clean_text(text or "")
