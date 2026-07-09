"""Lead prioritization. Fully deterministic — no AI.

Three dimensions, each 0-100:
- industry_score: how well the lead's company industry matches the ICP
- role_score: title/seniority/department relevance vs ICP target roles
- fit_score: experience signals — time in role and time at company

total_score = mean of the three, mapped to tiers:
  >= 75 hot | >= 55 warm | >= 35 nurture | < 35 deprioritized
"""

import re

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.icp import ICP
from app.models.lead import Lead, LeadTier
from app.models.lead_import import ImportStatus, LeadImport
from app.repositories.lead_repository import LeadRepository

SENIORITY_WEIGHTS = {
    "c-level": 100, "c level": 100, "cxo": 100, "founder": 100, "owner": 95, "partner": 90,
    "vp": 90, "vice president": 90, "head": 82, "director": 80, "senior manager": 65,
    "manager": 60, "lead": 50, "senior": 45, "entry": 20, "individual contributor": 25, "intern": 5,
}
TITLE_SENIORITY_HINTS = [
    (("chief", "ceo", "cto", "coo", "cfo", "cio", "founder", "president", "owner"), 100),
    (("vp", "vice president",), 90),
    (("head of", "head,"), 82),
    (("director",), 80),
    (("manager",), 60),
    (("lead",), 50),
]


def _tenure_to_years(raw: str | None) -> float | None:
    """Parses '3 years 2 months', '2.5 yrs', '18 months', '4' -> years."""
    if not raw:
        return None
    text = raw.lower().strip()
    years = 0.0
    matched = False
    year_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?)", text)
    month_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:months?|mos?)", text)
    if year_match:
        years += float(year_match.group(1))
        matched = True
    if month_match:
        years += float(month_match.group(1)) / 12
        matched = True
    if not matched:
        bare = re.match(r"^(\d+(?:\.\d+)?)$", text)
        if bare:
            years = float(bare.group(1))
            matched = True
    return years if matched else None


def score_industry(lead: Lead, icp: ICP) -> tuple[float, str]:
    targets = icp.target_industries or []
    industry = lead.company.industry if lead.company else None
    if not targets:
        return 70.0, "No industry filter on ICP; neutral-positive default."
    if not industry:
        return 30.0, "Company industry missing; low-confidence default."
    best = max(fuzz.token_set_ratio(industry.lower(), t.lower()) for t in targets)
    return round(float(best), 1), f"Best fuzzy match of '{industry}' against target industries: {best:.0f}%."


def score_role(lead: Lead, icp: ICP) -> tuple[float, str]:
    notes = []
    # Title vs target roles (60% of role score)
    title_component = 0.0
    if lead.title and icp.target_roles:
        best = max(fuzz.token_set_ratio(lead.title.lower(), r.lower()) for r in icp.target_roles)
        title_component = float(best)
        notes.append(f"Title match {best:.0f}%")
    elif not lead.title:
        notes.append("Title missing (0)")

    # Seniority (40% of role score)
    seniority_component = 0.0
    seniority = (lead.seniority or "").lower().strip()
    if seniority:
        seniority_component = float(SENIORITY_WEIGHTS.get(seniority, 40))
        for key, weight in SENIORITY_WEIGHTS.items():
            if key in seniority:
                seniority_component = float(weight)
                break
        notes.append(f"Seniority '{lead.seniority}' -> {seniority_component:.0f}")
    elif lead.title:
        title_lower = lead.title.lower()
        for keywords, weight in TITLE_SENIORITY_HINTS:
            if any(k in title_lower for k in keywords):
                seniority_component = float(weight)
                notes.append(f"Seniority inferred from title -> {weight}")
                break

    # Small deterministic boost when the department aligns with any target role wording
    boost = 0.0
    if lead.department and icp.target_roles:
        dept_match = max(fuzz.partial_ratio(lead.department.lower(), r.lower()) for r in icp.target_roles)
        if dept_match >= 70:
            boost = 5.0
            notes.append("Department aligned (+5)")

    score = min(100.0, title_component * 0.6 + seniority_component * 0.4 + boost)
    return round(score, 1), "; ".join(notes)


def score_fit(lead: Lead) -> tuple[float, str]:
    """Experience fit from role tenure and company tenure. Sweet spot: long enough to
    have authority and context, not so long they never change anything."""
    notes = []

    def tenure_score(years: float | None, label: str) -> float:
        if years is None:
            notes.append(f"{label} missing; neutral 50")
            return 50.0
        if years < 0.5:
            s = 35.0   # too new to buy
        elif years <= 3:
            s = 90.0   # established, still driving change
        elif years <= 6:
            s = 75.0
        elif years <= 10:
            s = 55.0
        else:
            s = 40.0   # long-tenured, likely settled
        notes.append(f"{label} {years:.1f}y -> {s:.0f}")
        return s

    role_component = tenure_score(_tenure_to_years(lead.time_in_role), "Role tenure")
    company_component = tenure_score(_tenure_to_years(lead.time_at_company), "Company tenure")
    score = round((role_component + company_component) / 2, 1)
    return score, "; ".join(notes)


def tier_for(total: float) -> LeadTier:
    if total >= 75:
        return LeadTier.HOT
    if total >= 55:
        return LeadTier.WARM
    if total >= 35:
        return LeadTier.NURTURE
    return LeadTier.DEPRIORITIZED


def score_lead(lead: Lead, icp: ICP) -> None:
    industry, industry_note = score_industry(lead, icp)
    role, role_note = score_role(lead, icp)
    fit, fit_note = score_fit(lead)
    total = round((industry + role + fit) / 3, 1)
    lead.industry_score = industry
    lead.role_score = role
    lead.fit_score = fit
    lead.total_score = total
    lead.tier = tier_for(total)
    lead.score_breakdown = {
        "industry": {"score": industry, "note": industry_note},
        "role": {"score": role, "note": role_note},
        "fit": {"score": fit, "note": fit_note},
    }


def score_import(db: Session, lead_import: LeadImport, icp: ICP) -> dict:
    repo = LeadRepository(db)
    counts = {t.value: 0 for t in LeadTier}
    for lead in repo.list_scorable_for_import(lead_import.id):
        score_lead(lead, icp)
        counts[lead.tier.value] += 1
    lead_import.status = ImportStatus.SCORED
    db.commit()
    return counts
