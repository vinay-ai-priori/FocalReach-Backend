from __future__ import annotations

from scraper.llm.schemas import EnrichmentResult
from scraper.schema.models import GrowthSignals, IcpSignals, Offering, Person, Product, ScrapeResult


def merge_enrichment(result: ScrapeResult, enrichment: EnrichmentResult | None) -> ScrapeResult:
    if enrichment is None:
        return result

    result.offering = Offering(
        products=[Product(name=p.name, description=p.description, category=p.category) for p in enrichment.offering.products],
        target_customer_hint=enrichment.offering.target_customer_hint,
        key_features=enrichment.offering.key_features,
        integrations=enrichment.offering.integrations,
    )
    result.icp_signals = IcpSignals(
        industries_served=enrichment.icp_signals.industries_served,
        use_cases=enrichment.icp_signals.use_cases,
        case_study_snippets=enrichment.icp_signals.case_study_snippets[:3],
    )
    result.people = [
        Person(name=p.name, title=p.title, linkedin_url=p.linkedin_url) for p in enrichment.people
    ]
    result.growth_signals = GrowthSignals(
        roles_hiring=enrichment.growth_signals.roles_hiring,
        tech_stack_mentions=enrichment.growth_signals.tech_stack_mentions,
    )
    return result
