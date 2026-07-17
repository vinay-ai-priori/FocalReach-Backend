from __future__ import annotations

from scraper.llm.schemas import EnrichmentResult
from scraper.schema.models import ScrapeResult


def merge_enrichment(result: ScrapeResult, enrichment: EnrichmentResult | None) -> ScrapeResult:
    if enrichment is None:
        return result

    result.offering = enrichment.offering
    result.icp_signals = enrichment.icp_signals
    result.people = enrichment.people

    result.social_proof.testimonials = enrichment.social_proof_testimonials
    result.social_proof.awards = enrichment.social_proof_awards
    result.social_proof.press_mentions = enrichment.social_proof_press_mentions

    return result
