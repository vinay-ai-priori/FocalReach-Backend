from __future__ import annotations

from scraper.schema.models import RawPage

SYSTEM_PROMPT = """You are an information-extraction assistant for a B2B sales \
intelligence tool. You are given text scraped from a company's website, \
grouped by page type. Extract only facts that are explicitly stated or \
strongly implied by the text. Do not invent names, numbers, or claims. \
Leave a field empty/null if it is not present in the text. This output will \
be used to build an Ideal Customer Profile and draft outbound emails, so \
prioritize concrete, specific, and quotable details over generic marketing \
language."""


def build_user_prompt(domain: str, raw_pages: list[RawPage], max_chars: int) -> str:
    buckets: dict[str, list[str]] = {}
    for page in raw_pages:
        if not page.extracted_text.strip():
            continue
        buckets.setdefault(page.page_type, []).append(f"[{page.url}]\n{page.extracted_text}")

    sections = []
    for page_type, texts in buckets.items():
        sections.append(f"## {page_type.upper()} PAGES\n" + "\n\n".join(texts))

    body = "\n\n".join(sections)
    if len(body) > max_chars:
        body = body[:max_chars]

    return f"Company domain: {domain}\n\n{body}"
