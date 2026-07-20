from __future__ import annotations

from scraper.schema.models import RawPage

SYSTEM_PROMPT = """You are an information-extraction assistant for a B2B sales \
intelligence tool. You are given text scraped from a company's website, \
grouped by page type. Extract only facts that are explicitly stated or \
strongly implied by the text. Do not invent names, numbers, or claims. \
Leave a field empty/null if it is not present in the text. This output will \
be used to qualify the company against an Ideal Customer Profile and draft \
outbound emails, so prioritize concrete, specific, and quotable details over \
generic marketing language.

Rules:
- offering: products/services, key features, integrations, and who they sell to.
- icp_signals: industries served, use cases, and AT MOST 3 case_study_snippets \
(pick the most specific ones).
- people: names, titles, and LinkedIn URLs only — no bios.
- growth_signals: roles being hired and technologies mentioned in job posts \
(from CAREERS pages).
- Do NOT extract industry classification, company size, or headquarters \
location — those come from another source."""

# Wordiest, lowest-density page types get a tighter per-bucket char budget so
# one long customers/news page can't crowd out product/team text in the prompt.
_BUCKET_MAX_CHARS: dict[str, int] = {
    "customers": 3_000,
    "news": 4_000,
}

# Deterministic ordering: highest-value buckets first so truncation (if any)
# eats the tail, not the signal.
_BUCKET_ORDER = ("home", "product", "customers", "team", "careers", "news", "other")


def build_user_prompt(domain: str, raw_pages: list[RawPage], max_chars: int) -> str:
    buckets: dict[str, list[str]] = {}
    for page in raw_pages:
        if not page.extracted_text.strip():
            continue
        buckets.setdefault(page.page_type, []).append(f"[{page.url}]\n{page.extracted_text}")

    sections = []
    for page_type in sorted(buckets, key=lambda t: _BUCKET_ORDER.index(t) if t in _BUCKET_ORDER else len(_BUCKET_ORDER)):
        text = "\n\n".join(buckets[page_type])
        cap = _BUCKET_MAX_CHARS.get(page_type)
        if cap is not None and len(text) > cap:
            text = text[:cap]
        sections.append(f"## {page_type.upper()} PAGES\n{text}")

    body = "\n\n".join(sections)
    if len(body) > max_chars:
        body = body[:max_chars]

    return f"Company domain: {domain}\n\n{body}"
