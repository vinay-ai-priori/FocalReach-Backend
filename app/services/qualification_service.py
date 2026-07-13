"""Company qualification against the ICP.

Flow (in order, cheapest first):
1. Gate 1 — geography match (deterministic). Fail -> rejected, no enrichment.
2. Gate 2 — employee size match (deterministic). Fail -> rejected, no enrichment.
   Missing data on either gate -> review (human decision), no enrichment.
3. Gate passers are enriched (website crawl + structured AI profile), then a single
   LLM call returns two 0-100 scores:
   - industry_match_score: intelligent (non-keyword) match of the company's CSV
     industry + description against the ICP target industries.
   - company_fit_score: how well the enriched website profile aligns with the ICP
     keywords and campaign objective.
4. Verdict from the average of the two scores:
   > 55 qualified (approved) | 40-55 review | < 40 disqualified (rejected).
"""

import json

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.company import Company, QualificationStatus
from app.models.icp import ICP
from app.models.lead_import import ImportStatus, LeadImport
from app.repositories.company_repository import CompanyRepository
from app.services.ai.openai_client import cached_json_completion
from app.services.enrichment_service import enrich_company

logger = get_logger(__name__)

QUALIFIED_THRESHOLD = 55.0
REVIEW_THRESHOLD = 40.0

# Common geography aliases so "USA" matches "United States", etc.
GEO_ALIASES = {
    "usa": "united states", "us": "united states", "u.s.": "united states",
    "uk": "united kingdom", "u.k.": "united kingdom", "uae": "united arab emirates",
    "deutschland": "germany", "holland": "netherlands",
}
REGION_MEMBERS = {
    "north america": {"united states", "canada", "mexico"},
    "europe": {
        "united kingdom", "germany", "france", "netherlands", "spain", "italy", "sweden", "norway",
        "denmark", "finland", "ireland", "belgium", "switzerland", "austria", "poland", "portugal",
    },
    "northern europe": {"united kingdom", "sweden", "norway", "denmark", "finland", "ireland", "netherlands"},
    "apac": {"australia", "japan", "singapore", "india", "china", "south korea", "new zealand"},
    "emea": set(),  # broad; treated as match-any for Europe/Middle East/Africa via region check below
}

SCORING_SYSTEM_PROMPT = """You are an impartial B2B sales-qualification analyst. You evaluate how well a prospect company matches an Ideal Customer Profile (ICP).
Judge ONLY from the inputs provided. Be strictly neutral: no bias toward or against any industry, region, company type, or business model. Do not assume facts that are not in the inputs. Missing or thin data should lower confidence, reflected as a mid-to-low score — never guess in either direction.

Score two independent dimensions, each 0-100:

1. industry_match_score — how well the prospect's industry and description (from the CSV) match the ICP target industries. This is an intelligent semantic judgment, NOT keyword matching: adjacent, overlapping, or sub-industries of a target count as strong matches; unrelated industries score low.

2. company_fit_score — how well the prospect's enriched website profile aligns with the ICP fit keywords and the campaign objective. Consider what the company does, who it serves, its technologies, and the problems it addresses — does this company look like one the campaign objective is aimed at? If the enrichment profile is unavailable, base this on the CSV description alone and cap your confidence accordingly.

Return ONLY a JSON object:
{
  "industry_match_score": number (0-100),
  "company_fit_score": number (0-100),
  "reasoning": string (2-4 sentences explaining both scores)
}"""


def _norm_geo(value: str) -> str:
    value = value.strip().lower()
    return GEO_ALIASES.get(value, value)


def check_geography(company: Company, icp: ICP) -> dict:
    targets = [_norm_geo(g) for g in (icp.target_geographies or [])]
    if not targets:
        return {"check": "geography", "result": "pass", "detail": "No geography filter set on the ICP."}
    country = company.country and _norm_geo(company.country)
    if not country:
        return {"check": "geography", "result": "unknown", "detail": "Company country is missing from the CSV."}
    for target in targets:
        if target == country or fuzz.ratio(target, country) >= 90:
            return {"check": "geography", "result": "pass", "detail": f"{company.country} matches target '{target}'."}
        members = REGION_MEMBERS.get(target)
        if members is not None and (not members or country in members):
            return {"check": "geography", "result": "pass", "detail": f"{company.country} is within region '{target}'."}
    return {"check": "geography", "result": "fail", "detail": f"{company.country} is outside the target geographies."}


def _parse_range_label(label: str) -> tuple[int, int | None] | None:
    import re

    numbers = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", label)]
    if not numbers:
        return None
    if "+" in label or len(numbers) == 1:
        return numbers[0], (numbers[1] if len(numbers) > 1 else None)
    return numbers[0], numbers[1]


def check_employee_size(company: Company, icp: ICP) -> dict:
    ranges = icp.company_size_ranges or []
    if not ranges:
        return {"check": "employee_size", "result": "pass", "detail": "No company-size filter set on the ICP."}

    count = company.employee_count
    if count is None and company.employee_range:
        parsed = _parse_range_label(company.employee_range)
        if parsed:
            low, high = parsed
            count = (low + high) // 2 if high else low
    if count is None:
        return {"check": "employee_size", "result": "unknown", "detail": "Employee count is missing from the CSV."}

    for r in ranges:
        low = int(r.get("min") or 0)
        high = r.get("max")
        if count >= low and (high is None or count <= int(high)):
            return {"check": "employee_size", "result": "pass", "detail": f"{count} employees fits '{r.get('label', f'{low}+')}'."}
    return {"check": "employee_size", "result": "fail", "detail": f"{count} employees is outside all target size ranges."}


def _to_score(value) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def score_company_fit(company: Company, icp: ICP) -> tuple[float, float, str]:
    """One LLM call -> (industry_match_score, company_fit_score, reasoning)."""
    profile = company.enrichment_profile
    user_prompt = (
        "ICP (Ideal Customer Profile):\n"
        f"- Target industries: {icp.target_industries or []}\n"
        f"- Fit keywords: {icp.target_keywords or []}\n"
        f"- Campaign objective: {icp.campaign_objective or 'Not specified'}\n\n"
        "PROSPECT COMPANY (from CSV):\n"
        f"- Name: {company.name}\n"
        f"- Industry: {company.industry or 'Not provided'}\n"
        f"- Description: {company.description or 'Not provided'}\n\n"
        "PROSPECT COMPANY (enriched from their website):\n"
        f"{json.dumps(profile, indent=2) if profile else 'Enrichment unavailable.'}"
    )
    data, _ = cached_json_completion(SCORING_SYSTEM_PROMPT, user_prompt)
    return (
        _to_score(data.get("industry_match_score")),
        _to_score(data.get("company_fit_score")),
        str(data.get("reasoning") or ""),
    )


def qualify_company(db: Session, company: Company, icp: ICP) -> tuple[QualificationStatus, list[dict]]:
    """Gates first (no cost), then enrichment + LLM scoring for gate passers."""
    checks = [check_geography(company, icp), check_employee_size(company, icp)]
    gate_results = {c["result"] for c in checks}

    if "fail" in gate_results:
        return QualificationStatus.REJECTED, checks
    if "unknown" in gate_results:
        return QualificationStatus.REVIEW, checks

    # Both gates passed -> enrich, then score. Enrichment failure is non-fatal.
    enrich_company(db, company)

    try:
        industry_score, fit_score, reasoning = score_company_fit(company, icp)
    except Exception as exc:
        logger.warning("LLM qualification scoring failed for %s: %s", company.name, exc)
        checks.append({"check": "fit_scoring", "result": "unknown", "detail": "AI scoring failed — routed to review."})
        return QualificationStatus.REVIEW, checks

    company.industry_match_score = industry_score
    company.company_fit_score = fit_score
    company.qualification_reasoning = reasoning

    average = round((industry_score + fit_score) / 2, 1)
    if average > QUALIFIED_THRESHOLD:
        status = QualificationStatus.APPROVED
        result = "pass"
    elif average >= REVIEW_THRESHOLD:
        status = QualificationStatus.REVIEW
        result = "unknown"
    else:
        status = QualificationStatus.REJECTED
        result = "fail"

    checks.append({
        "check": "industry_match",
        "result": result,
        "detail": f"Industry match score {industry_score:.0f}/100.",
    })
    checks.append({
        "check": "company_fit",
        "result": result,
        "detail": f"Company fit score {fit_score:.0f}/100 (avg {average:.0f} -> {status.value}). {reasoning}".strip(),
    })
    return status, checks


# Commit every N qualified companies: batching cuts DB round-trips while keeping the
# uncommitted window small (both for RAM held and for progress lost on a worker restart).
QUALIFY_COMMIT_BATCH_SIZE = 10


# Big per-company payloads (crawled website text, AI profile, check details). The session
# runs with expire_on_commit=False, so once committed these would otherwise sit in RAM for
# the whole task — expire them right after their batch commits so the memory is reclaimed.
_HEAVY_COMPANY_FIELDS = ["enrichment_content", "enrichment_profile", "qualification_checks"]


def qualify_import(db: Session, lead_import: LeadImport, icp: ICP) -> dict:
    repo = CompanyRepository(db)
    counts = {"approved": 0, "rejected": 0, "review": 0}
    batch: list = []
    for company in repo.list_for_import(lead_import.id):
        # Skip human decisions AND companies already qualified in a previous run of this
        # import (append mode: only new companies enter the pipeline). A re-run clears
        # qualification_checks/override first, so those are re-processed here.
        if company.qualification_override or company.qualification_checks is not None:
            counts[company.qualification_status.value] = counts.get(company.qualification_status.value, 0) + 1
            continue
        status, checks = qualify_company(db, company, icp)
        company.qualification_status = status
        company.qualification_checks = checks
        counts[status.value] += 1
        batch.append(company)
        if len(batch) >= QUALIFY_COMMIT_BATCH_SIZE:
            db.commit()  # flush the batch so long imports survive worker restarts
            for done in batch:
                db.expire(done, _HEAVY_COMPANY_FIELDS)
            batch = []
    lead_import.status = ImportStatus.QUALIFIED
    db.commit()  # final commit covers any remaining partial batch
    return counts
