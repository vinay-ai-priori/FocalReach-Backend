"""ICP generation (AI) and CRUD (deterministic)."""

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.company_intelligence import CompanyIntelligence
from app.models.icp import ICP
from app.repositories.icp_repository import ICPRepository
from app.services.ai.openai_client import cached_json_completion

SYSTEM_PROMPT = """You are a B2B go-to-market strategist. Given a company profile, generate their Ideal Customer Profile for outbound sales.
Return ONLY a JSON object with exactly these keys:
{
  "campaign_objective": string (one sentence, e.g. "Book discovery calls with ops leaders at mid-market manufacturers"),
  "target_industries": [string] (4-6 industries most likely to buy),
  "company_size_ranges": [{"min": int, "max": int or null, "label": string}] (e.g. {"min": 201, "max": 1000, "label": "201-1,000"}),
  "target_roles": [string] (5-7 job titles of likely buyers),
  "target_seniorities": [string] (subset of: "C-Level", "VP", "Director", "Manager", "Head"),
  "target_geographies": [string] (countries or regions),
  "outreach_tone": string (one of: "consultative", "direct", "formal")
}"""


def generate_icp(db: Session, intelligence: CompanyIntelligence, user_id: int | None = None) -> ICP:
    repo = ICPRepository(db)

    existing = repo.get_active_for_intelligence(intelligence.id, user_id)
    if existing:
        return existing

    user_prompt = (
        f"Company: {intelligence.company_name}\n"
        f"Industry: {intelligence.industry}\n"
        f"Business model: {intelligence.business_model}\n"
        f"Summary: {intelligence.summary}\n"
        f"Services: {intelligence.services}\n"
        f"Geography served: {intelligence.geography}\n"
        f"Target customers: {intelligence.target_customers}\n"
        f"Value propositions: {intelligence.value_propositions}"
    )
    data, _ = cached_json_completion(SYSTEM_PROMPT, user_prompt)

    icp = ICP(
        company_intelligence_id=intelligence.id,
        user_id=user_id,
        campaign_objective=data.get("campaign_objective"),
        target_industries=data.get("target_industries") or [],
        company_size_ranges=data.get("company_size_ranges") or [],
        target_roles=data.get("target_roles") or [],
        target_seniorities=data.get("target_seniorities") or [],
        target_geographies=data.get("target_geographies") or [],
        outreach_tone=(data.get("outreach_tone") or "consultative").lower(),
        is_ai_generated=True,
    )
    return repo.create(icp)


def update_icp(db: Session, icp_id: int, fields: dict) -> ICP:
    repo = ICPRepository(db)
    icp = repo.get(icp_id)
    if not icp:
        raise NotFoundError(f"ICP {icp_id} not found.")
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        fields["is_ai_generated"] = False
        fields["version"] = icp.version + 1
        icp = repo.update(icp, **fields)
    return icp
