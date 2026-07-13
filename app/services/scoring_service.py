"""Lead prioritization per the "Role Score & Signal Score — Logic Document".

Deterministic and fully auditable. Role scoring runs in two phases:
- Phase 1 — ICP role match: if the lead's title semantically matches one of the ICP
  Builder's target roles (embedding cosine >= ROLE_MATCH_THRESHOLD, i.e. effectively
  the same role), award the full role score.
- Phase 2 — fallback: otherwise score from the fixed tier table below, exactly as
  before. Signal and company-fit are unchanged (pure if/else, no AI).

Three dimensions:
- role_score (max 30): ICP target-role match (full score) or job-title keyword match
  against a fixed tier table with a company-size modifier. (Doc Part 1)
- signal_score (max 30): tenure in current role (12) + total career experience (13),
  from fixed bracket tables. (Doc Part 2)
- company_fit_score (max 40): average of the company's LLM qualification scores
  (industry match + company fit, each 0-100). Inherited from company qualification.
  (Doc Part 3)

Engagement score (15) is intentionally not implemented yet; until it lands, its points
are redistributed +5 to signal (25->30) and +10 to company fit (30->40) by scaling the
document values — the underlying lookup tables are unchanged. When engagement is added,
drop the ROLE_MAX/SIGNAL_MAX/COMPANY_FIT_MAX overrides back to the document's 30/25/30.

total_score = role + signal + company_fit (max 100), mapped to tiers:
  >= 80 hot | >= 50 warm | >= 25 nurture | < 25 deprioritized
"""

import re

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.icp import ICP
from app.models.lead import Lead, LeadTier
from app.models.lead_import import ImportStatus, LeadImport
from app.repositories.lead_repository import LeadRepository
from app.services.ai.embedding_service import cosine, get_header_vectors, normalize_header

logger = get_logger(__name__)

# Dimension maxima with engagement's 15 points redistributed (+5 signal, +10 company fit).
ROLE_MAX = 30    # document base: 30 (unchanged)
SIGNAL_MAX = 30  # document base: 25
COMPANY_FIT_MAX = 40  # document base: 30

# ---------- Part 1: Role Score ----------

# (tier name, keywords to match in title, base points). Checked top-down; a title
# matching multiple tiers gets the highest (list is ordered highest-first).
ROLE_TIERS: list[tuple[str, tuple[str, ...], int]] = [
    ("Tier 1 — Decision Maker",
     ("ceo", "chief executive", "founder", "co-founder", "owner", "managing director", "md", "president"), 30),
    ("Tier 2 — Senior Executive",
     ("coo", "cto", "cfo", "cio", "chief operating", "chief technology", "chief financial", "chief information"), 25),
    ("Tier 3 — Director / VP",
     ("vp", "vice president", "director", "head of", "group head", "regional head"), 20),
    ("Tier 4 — Senior Manager",
     ("senior manager", "principal", "senior lead", "senior consultant"), 15),
    ("Tier 5 — Manager",
     ("manager", "team lead", "lead", "supervisor"), 10),
    ("Tier 6 — Junior / Other",
     ("executive", "coordinator", "analyst", "associate", "consultant", "officer", "specialist", "junior", "assistant"), 5),
]
NO_MATCH_POINTS = 2
MISSING_TITLE_POINTS = 0

# Company size modifier per doc 1.3: {size bracket: {tier index (0-based): delta}}
# Brackets: under 50 handled separately (Tier 1 unchanged, all others -3).
SIZE_MODIFIERS: dict[str, dict[int, int]] = {
    "201-1000": {0: -8, 2: +5},
    "1001-5000": {0: -12, 1: +3, 2: +3},
    "5000+": {0: -18, 2: +8},
}


def _title_contains(title: str, keyword: str) -> bool:
    # Word-boundary match so e.g. "md" doesn't match inside "MDx Systems".
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", title) is not None


def _match_role_tier(title: str | None) -> tuple[str | None, int]:
    """Returns (tier index name, base points). Highest matching tier wins."""
    if not title or not title.strip():
        return None, MISSING_TITLE_POINTS
    lowered = title.lower()
    for name, keywords, points in ROLE_TIERS:
        if any(_title_contains(lowered, k) for k in keywords):
            return name, points
    return "No Match", NO_MATCH_POINTS


def _company_employee_count(lead: Lead) -> int | None:
    company = lead.company
    if not company:
        return None
    if company.employee_count is not None:
        return company.employee_count
    if company.employee_range:
        numbers = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", company.employee_range)]
        if numbers:
            return (numbers[0] + numbers[1]) // 2 if len(numbers) > 1 else numbers[0]
    return None


def _size_modifier(tier_index: int | None, employee_count: int | None) -> tuple[int, str]:
    if tier_index is None or employee_count is None:
        return 0, "No size modifier (missing tier or company size)."
    if employee_count < 50:
        # CEO/Founder is the sole decision maker: Tier 1 unchanged, all others -3.
        if tier_index == 0:
            return 0, "Under 50 employees: Tier 1 unchanged."
        return -3, "Under 50 employees: -3 for non-Tier-1."
    if employee_count <= 200:
        return 0, "50-200 employees: no modifier."
    if employee_count <= 1000:
        bracket = "201-1000"
    elif employee_count <= 5000:
        bracket = "1001-5000"
    else:
        bracket = "5000+"
    delta = SIZE_MODIFIERS[bracket].get(tier_index, 0)
    return delta, f"{bracket} employees: {'+' if delta >= 0 else ''}{delta} for tier {tier_index + 1}."


def build_role_match_index(db: Session, icp: ICP, titles: list[str | None]) -> dict[str, tuple[str, float]]:
    """Phase 1 pre-pass: {normalized title -> (matched ICP role, similarity)} for every
    unique lead title that matches one of the ICP's target roles at or above the strict
    threshold. Embeddings are batched and Postgres-cached (one API call for the misses),
    so cost is per unique text, not per lead. Returns {} when the ICP has no target
    roles or the embedding call fails — scoring then falls back to tier logic alone."""
    roles = [r.strip() for r in (icp.target_roles or []) if isinstance(r, str) and r.strip()]
    unique_titles = sorted({normalize_header(t) for t in titles if t and t.strip()})
    if not roles or not unique_titles:
        return {}
    try:
        vectors = get_header_vectors(db, roles + unique_titles)
    except Exception as exc:
        logger.warning("Role-match embeddings unavailable, falling back to tier logic: %s", exc)
        return {}

    role_vectors = [(r, vectors.get(normalize_header(r))) for r in roles]
    index: dict[str, tuple[str, float]] = {}
    for title in unique_titles:
        title_vec = vectors.get(title)
        if not title_vec:
            continue
        best_role, best_sim = None, 0.0
        for role, role_vec in role_vectors:
            if not role_vec:
                continue
            sim = cosine(title_vec, role_vec)
            if sim > best_sim:
                best_role, best_sim = role, sim
        if best_role is not None and best_sim >= settings.ROLE_MATCH_THRESHOLD:
            index[title] = (best_role, best_sim)
    return index


def score_role(lead: Lead, role_match_index: dict[str, tuple[str, float]] | None = None) -> tuple[float, str]:
    # Phase 1: full score when the title IS one of the ICP's target roles.
    if role_match_index and lead.title and lead.title.strip():
        match = role_match_index.get(normalize_header(lead.title))
        if match:
            role, sim = match
            return float(ROLE_MAX), (
                f"Title '{lead.title}' matches ICP target role '{role}' "
                f"(similarity {sim:.2f}) -> full {ROLE_MAX}/{ROLE_MAX}."
            )
    # Phase 2: fixed tier table + company-size modifier.
    tier_name, base = _match_role_tier(lead.title)
    if tier_name is None:
        return 0.0, "Title missing -> 0."
    if tier_name == "No Match":
        scaled = round(NO_MATCH_POINTS * ROLE_MAX / 30, 1)
        return scaled, f"Title '{lead.title}' matched no tier -> {scaled}/{ROLE_MAX}."
    tier_index = next(i for i, (name, _, _) in enumerate(ROLE_TIERS) if name == tier_name)
    delta, modifier_note = _size_modifier(tier_index, _company_employee_count(lead))
    doc_points = max(0, min(30, base + delta))
    scaled = round(doc_points * ROLE_MAX / 30, 1)
    return scaled, f"{tier_name} ({base} base). {modifier_note} Doc score {doc_points}/30 -> {scaled}/{ROLE_MAX}."


# ---------- Part 2: Signal Score ----------

def _to_years(raw: str | None) -> float | None:
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


def _tenure_points(years: float | None) -> int:
    if years is None:
        return 0
    if years >= 5:
        return 12
    if years >= 3:
        return 10
    if years >= 2:
        return 8
    if years >= 1:
        return 5
    return 2


def _experience_points(years: float | None) -> int:
    if years is None:
        return 0
    if years >= 20:
        return 13
    if years >= 15:
        return 11
    if years >= 10:
        return 9
    if years >= 7:
        return 6
    if years >= 4:
        return 3
    return 1


def score_signal(lead: Lead) -> tuple[float, str]:
    notes = []
    tenure_years = _to_years(lead.time_in_role)
    tenure_pts = _tenure_points(tenure_years)
    if tenure_years is None:
        notes.append("Tenure missing -> 0")
    else:
        notes.append(f"Tenure {tenure_years:.1f}y -> {tenure_pts}/12")

    experience_years = _to_years(lead.years_experience)
    estimated = False
    if experience_years is None and tenure_years is not None:
        # Doc: if total experience is unavailable, use tenure as a proxy and flag it.
        experience_years = tenure_years
        estimated = True
    experience_pts = _experience_points(experience_years)
    if experience_years is None:
        notes.append("Experience missing -> 0")
    else:
        notes.append(f"Experience {experience_years:.1f}y -> {experience_pts}/13" + (" (estimated from tenure)" if estimated else ""))

    doc_total = min(25, tenure_pts + experience_pts)
    scaled = round(doc_total * SIGNAL_MAX / 25, 1)
    return scaled, "; ".join(notes) + f". Doc score {doc_total}/25 -> {scaled}/{SIGNAL_MAX}."


# ---------- Part 3: Company Fit Score (from qualification) ----------

def score_company_fit(lead: Lead) -> tuple[float, str]:
    company = lead.company
    if not company or company.industry_match_score is None or company.company_fit_score is None:
        return 0.0, f"Company qualification scores unavailable -> 0/{COMPANY_FIT_MAX}."
    average = (company.industry_match_score + company.company_fit_score) / 2
    scaled = round(average * COMPANY_FIT_MAX / 100, 1)
    return scaled, (
        f"Industry match {company.industry_match_score:.0f} + company fit {company.company_fit_score:.0f} "
        f"-> avg {average:.0f}/100 -> {scaled}/{COMPANY_FIT_MAX}."
    )


# ---------- Total + tiers ----------

def tier_for(total: float) -> LeadTier:
    if total >= 80:
        return LeadTier.HOT
    if total >= 50:
        return LeadTier.WARM
    if total >= 25:
        return LeadTier.NURTURE
    return LeadTier.DEPRIORITIZED


def score_lead(lead: Lead, icp: ICP, role_match_index: dict[str, tuple[str, float]] | None = None) -> None:
    role, role_note = score_role(lead, role_match_index)
    signal, signal_note = score_signal(lead)
    company_fit, fit_note = score_company_fit(lead)
    total = round(role + signal + company_fit, 1)
    lead.role_score = role
    lead.signal_score = signal
    lead.company_fit_score = company_fit
    lead.total_score = total
    lead.tier = tier_for(total)
    lead.score_breakdown = {
        "role": {"score": role, "note": role_note},
        "signal": {"score": signal, "note": signal_note},
        "company_fit": {"score": company_fit, "note": fit_note},
    }


def score_import(db: Session, lead_import: LeadImport, icp: ICP) -> dict:
    repo = LeadRepository(db)
    leads = repo.list_scorable_for_import(lead_import.id)
    role_match_index = build_role_match_index(db, icp, [lead.title for lead in leads])
    counts = {t.value: 0 for t in LeadTier}
    for lead in leads:
        score_lead(lead, icp, role_match_index)
        counts[lead.tier.value] += 1
    lead_import.status = ImportStatus.SCORED
    db.commit()
    return counts
