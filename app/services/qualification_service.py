"""Company qualification against the ICP. Fully deterministic — no AI.

Each company is checked on geography, industry, and employee size:
- all checks pass  -> approved
- any check fails  -> rejected
- any check has no data (or is borderline) -> review (human decision)
"""

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.company import Company, QualificationStatus
from app.models.icp import ICP
from app.models.lead_import import ImportStatus, LeadImport
from app.repositories.company_repository import CompanyRepository

INDUSTRY_MATCH_THRESHOLD = 78.0

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


def check_industry(company: Company, icp: ICP) -> dict:
    targets = icp.target_industries or []
    if not targets:
        return {"check": "industry", "result": "pass", "detail": "No industry filter set on the ICP."}
    if not company.industry:
        return {"check": "industry", "result": "unknown", "detail": "Company industry is missing from the CSV."}
    best_target, best_score = None, 0.0
    for target in targets:
        score = fuzz.token_set_ratio(company.industry.lower(), target.lower())
        if score > best_score:
            best_target, best_score = target, score
    if best_score >= INDUSTRY_MATCH_THRESHOLD:
        return {"check": "industry", "result": "pass", "detail": f"'{company.industry}' matches '{best_target}' ({best_score:.0f}%)."}
    if best_score >= 55:
        return {"check": "industry", "result": "unknown", "detail": f"'{company.industry}' is a borderline match to '{best_target}' ({best_score:.0f}%) — needs review."}
    return {"check": "industry", "result": "fail", "detail": f"'{company.industry}' does not match any target industry."}


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


def qualify_company(company: Company, icp: ICP) -> tuple[QualificationStatus, list[dict]]:
    checks = [check_geography(company, icp), check_industry(company, icp), check_employee_size(company, icp)]
    results = {c["result"] for c in checks}
    if "fail" in results:
        status = QualificationStatus.REJECTED
    elif "unknown" in results:
        status = QualificationStatus.REVIEW
    else:
        status = QualificationStatus.APPROVED
    return status, checks


def qualify_import(db: Session, lead_import: LeadImport, icp: ICP) -> dict:
    repo = CompanyRepository(db)
    counts = {"approved": 0, "rejected": 0, "review": 0}
    for company in repo.list_for_import(lead_import.id):
        if company.qualification_override:
            counts[company.qualification_status.value] = counts.get(company.qualification_status.value, 0) + 1
            continue
        status, checks = qualify_company(company, icp)
        company.qualification_status = status
        company.qualification_checks = checks
        counts[status.value] += 1
    lead_import.status = ImportStatus.QUALIFIED
    db.commit()
    return counts
