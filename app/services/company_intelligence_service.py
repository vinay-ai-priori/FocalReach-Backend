"""Company Intelligence: AI turns crawled website content into a structured company profile.
This is one of the only three AI touchpoints (company intelligence, ICP generation, email drafts)."""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company_intelligence import CompanyIntelligence
from app.models.website_analysis import WebsiteAnalysis
from app.repositories.company_intelligence_repository import CompanyIntelligenceRepository
from app.services.ai.openai_client import cached_json_completion

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a B2B company analyst. Given raw website text, produce a rigorous JSON company profile.
Return ONLY a JSON object with exactly these keys:
{
  "company_name": string,
  "summary": string (3-4 sentence overview of what the company does),
  "industry": string (primary industry),
  "sub_industries": [string],
  "services": [{"name": string, "description": string}],
  "business_model": string (e.g. "B2B SaaS", "Professional Services", "Manufacturer", "Marketplace"),
  "geography": [string] (markets/regions served),
  "company_size": string (best estimate, e.g. "11-50 employees", "Unknown"),
  "technology_signals": [{"signal": string, "evidence": string}],
  "business_signals": [{"signal": string, "evidence": string}],
  "value_propositions": [string],
  "target_customers": [string]
}
Base every field strictly on the provided text. Use "Unknown" or empty arrays when the text has no evidence."""


def generate_company_intelligence(db: Session, analysis: WebsiteAnalysis) -> CompanyIntelligence:
    repo = CompanyIntelligenceRepository(db)

    existing = repo.get_by_analysis(analysis.id)
    if existing:
        logger.info("Company intelligence already exists for analysis %s; reusing", analysis.id)
        return existing

    user_prompt = (
        f"Website: {analysis.url}\n"
        f"Page title: {analysis.page_title or 'N/A'}\n"
        f"Meta description: {analysis.meta_description or 'N/A'}\n\n"
        f"Website content:\n{(analysis.extracted_content or '')[:24000]}"
    )
    data, was_cached = cached_json_completion(SYSTEM_PROMPT, user_prompt)

    intelligence = CompanyIntelligence(
        website_analysis_id=analysis.id,
        company_name=data.get("company_name"),
        summary=data.get("summary"),
        industry=data.get("industry"),
        sub_industries=data.get("sub_industries") or [],
        services=data.get("services") or [],
        business_model=data.get("business_model"),
        geography=data.get("geography") or [],
        company_size=data.get("company_size"),
        technology_signals=data.get("technology_signals") or [],
        business_signals=data.get("business_signals") or [],
        value_propositions=data.get("value_propositions") or [],
        target_customers=data.get("target_customers") or [],
        ai_model=settings.OPENAI_MODEL,
        ai_cached=was_cached,
    )
    return repo.create(intelligence)
