"""ICP generation (AI) and CRUD (deterministic)."""

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.campaign import Campaign
from app.models.company_intelligence import CompanyIntelligence
from app.models.icp import ICP
from app.repositories.icp_repository import ICPRepository
from app.services.ai.openai_client import cached_json_completion

SYSTEM_PROMPT = """You are a B2B go-to-market strategist. Given a company profile, generate their Ideal Customer Profile for outbound sales.
Return ONLY a JSON object with exactly these keys:
{
  "campaign_objectives": [string, string, string] (exactly 3 distinct one-sentence options, each a plausible campaign goal, e.g. "Book discovery calls with ops leaders at mid-market manufacturers" — vary the angle across the 3: e.g. one focused on booking meetings, one on a specific buyer pain point, one on a specific segment or use case),
  "target_industries": [string] (4-6 industries most likely to buy),
  "target_roles": [string] (5-7 job titles of likely buyers),
  "target_keywords": [string] (5-8 keywords/phrases that signal a strong fit — technologies, pain points, or intent terms),
  "target_seniorities": [string] (subset of: "C-Level", "VP", "Director", "Manager", "Head")
}"""


def generate_icp(db: Session, intelligence: CompanyIntelligence, campaign: Campaign) -> ICP:
    repo = ICPRepository(db)

    existing = repo.get_active_for_campaign(campaign.id)
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

    objectives = [o for o in (data.get("campaign_objectives") or []) if isinstance(o, str) and o.strip()]

    icp = ICP(
        campaign_id=campaign.id,
        company_intelligence_id=intelligence.id,
        campaign_objective=objectives[0] if objectives else None,
        campaign_objective_options=objectives,
        target_industries=data.get("target_industries") or [],
        # Size ranges, geographies, and tone are deliberate user choices, never
        # AI-suggested: invisible AI defaults here previously widened the
        # qualification filters behind the user's back.
        company_size_ranges=[],
        target_roles=data.get("target_roles") or [],
        target_keywords=data.get("target_keywords") or [],
        target_seniorities=data.get("target_seniorities") or [],
        target_geographies=[],
        outreach_tone="consultative",
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
