"""Company qualification against the ICP — two stages, cheapest first.

Stage 1 (deterministic, free): geography + employee-size gates over the whole
import in one pass. Fail -> rejected; missing data -> review; verdicts persisted
in a single commit. Gate passers move to stage 2.

Stage 2 (waves of QUALIFY_PARALLELISM): concurrent enrichment (website scrape ->
structured AI profile) then concurrent LLM scoring — one call per company returns:
- industry_match_score: intelligent (non-keyword) match of the company's CSV
  industry + description against the ICP target industries.
- company_fit_score: how well the enriched website profile aligns with the ICP
  keywords and campaign objective.
- solvable_pain_points: prospect problems the sender's services can solve.
Verdict from the average of the two scores:
>= 55 qualified (approved) | < 55 needs review. An LLM score can never REJECT —
rejection is reserved for the deterministic gates (geography / employee size).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import pycountry
import pycountry_convert
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company import Company, CompanyQualification, QualificationStatus
from app.models.company_intelligence import CompanyIntelligence
from app.models.icp import ICP
from app.models.lead_import import ImportStatus, LeadImport
from app.repositories.company_repository import CompanyRepository
from app.services.ai.openai_client import cached_json_completion
from app.services.enrichment_service import EnrichmentSession

logger = get_logger(__name__)

QUALIFIED_THRESHOLD = 55.0  # average >= this -> approved; below -> needs review

# Names pycountry can't resolve directly: common short names, endonyms, initialism
# variants. Values must be resolvable by pycountry.countries.lookup().
GEO_ALIASES = {
    "uk": "GB", "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "uae": "AE", "türkiye": "TR", "turkey": "TR",
    "russia": "RU", "ivory coast": "CI", "holland": "NL", "deutschland": "DE",
    "españa": "ES", "espana": "ES", "italia": "IT", "sverige": "SE", "norge": "NO",
    "suomi": "FI", "österreich": "AT", "osterreich": "AT", "schweiz": "CH",
    "turkiye": "TR", "north korea": "KP", "laos": "LA", "syria": "SY", "iran": "IR",
    "venezuela": "VE", "bolivia": "BO", "tanzania": "TZ", "moldova": "MD",
    "brunei": "BN", "cape verde": "CV", "vatican": "VA", "palestine": "PS",
    "macedonia": "MK", "swaziland": "SZ", "burma": "MM",
}

_MIDDLE_EAST = {"AE", "SA", "QA", "KW", "BH", "OM", "IL", "JO", "LB", "IQ", "IR", "SY", "YE", "PS", "TR", "EG"}
_LATAM_EXTRA = {"MX", "GT", "HN", "SV", "NI", "CR", "PA", "BZ", "CU", "DO", "HT", "PR"}

# Business regions an ICP may target, defined by continent codes (from ISO 3166 via
# pycountry-convert: NA, SA, EU, AS, AF, OC) plus curated country sets where no ISO
# grouping exists. Any country on Earth resolves into these — nothing is hand-listed
# per region except genuinely non-ISO groupings (Middle East, Nordics, ...).
REGION_DEFS: dict[str, dict] = {
    "europe": {"continents": {"EU"}},
    "north america": {"continents": {"NA"}},
    "south america": {"continents": {"SA"}},
    "latam": {"continents": {"SA"}, "countries": _LATAM_EXTRA},
    "latin america": {"continents": {"SA"}, "countries": _LATAM_EXTRA},
    "asia": {"continents": {"AS"}},
    "africa": {"continents": {"AF"}},
    "oceania": {"continents": {"OC"}},
    "americas": {"continents": {"NA", "SA"}},
    "apac": {"continents": {"AS", "OC"}},
    "asia pacific": {"continents": {"AS", "OC"}},
    "emea": {"continents": {"EU", "AF"}, "countries": _MIDDLE_EAST},
    "middle east": {"countries": _MIDDLE_EAST},
    "gcc": {"countries": {"AE", "SA", "QA", "KW", "BH", "OM"}},
    "nordics": {"countries": {"SE", "NO", "DK", "FI", "IS"}},
    "northern europe": {"countries": {"GB", "IE", "SE", "NO", "DK", "FI", "IS", "NL", "EE", "LV", "LT"}},
    "dach": {"countries": {"DE", "AT", "CH"}},
    "benelux": {"countries": {"BE", "NL", "LU"}},
    "anz": {"countries": {"AU", "NZ"}},
    "global": {"any": True},
    "worldwide": {"any": True},
    "international": {"any": True},
}

SCORING_SYSTEM_PROMPT = """You are an impartial B2B sales-qualification analyst. You evaluate how well a prospect company matches an Ideal Customer Profile (ICP).
Judge ONLY from the inputs provided. Be strictly neutral: no bias toward or against any industry, region, company type, or business model. Do not assume facts that are not in the inputs. Missing or thin data should lower confidence, reflected as a mid-to-low score — never guess in either direction. If there is essentially NO usable evidence about the prospect (no industry, no description, no enrichment), return 40-50 on both scores: absence of evidence means a human should review, not an automatic rejection.

Score two independent dimensions, each 0-100:

1. industry_match_score — how well the prospect's industry and description (from the CSV) match the ICP target industries. This is an intelligent semantic judgment, NOT keyword matching: adjacent, overlapping, or sub-industries of a target count as strong matches; unrelated industries score low.

2. company_fit_score — how well the prospect's enriched website profile aligns with the ICP fit keywords and the campaign objective. Consider what the company does, who it serves, its technologies, and the problems it addresses — does this company look like one the campaign objective is aimed at? If the enrichment profile is unavailable, base this on the CSV description alone and cap your confidence accordingly.

Additionally, extract solvable pain points: concrete problems the PROSPECT company plausibly faces that the SENDER company's services can solve. Strict grounding rules:
- Each pain point must be supported by specific evidence in the prospect's data (their offerings, use cases, technologies, news, or description) — quote or paraphrase that evidence.
- Each must map to a specific sender service or value proposition — name it in solved_by.
- No generic filler ("needs more customers"), no speculation beyond the evidence. If nothing credible is supported, return an empty list. 0-4 items maximum.

Return ONLY a JSON object:
{
  "industry_match_score": number (0-100),
  "company_fit_score": number (0-100),
  "reasoning": string (2-4 sentences explaining both scores),
  "solvable_pain_points": [
    {"pain_point": string, "evidence": string, "solved_by": string}
  ]
}"""


@lru_cache(maxsize=4096)
def _canon_country(value: str) -> str | None:
    """Resolve any country spelling to its ISO alpha-2 code, or None if unrecognizable.

    Order: alias map -> pycountry lookup (names, official names, ISO codes) ->
    dot/space-stripped retry ("U.S.A.") -> fuzzy match against all country names
    (typos like "Untied States"; ratio 90 keeps Austria/Australia apart).
    """
    raw = value.strip().lower()
    if not raw:
        return None
    candidates = [GEO_ALIASES.get(raw, raw), raw.replace(".", "").replace(" ", "")]
    for candidate in candidates:
        try:
            return pycountry.countries.lookup(candidate).alpha_2
        except LookupError:
            continue
    best, best_score = None, 0.0
    for c in pycountry.countries:
        for name in filter(None, (getattr(c, "name", None), getattr(c, "common_name", None), getattr(c, "official_name", None))):
            score = fuzz.ratio(raw, name.lower())
            if score > best_score:
                best, best_score = c.alpha_2, score
    return best if best_score >= 90 else None


@lru_cache(maxsize=512)
def _continent_of(alpha2: str) -> str | None:
    try:
        return pycountry_convert.country_alpha2_to_continent_code(alpha2)
    except KeyError:
        return None


def _in_region(alpha2: str, region: dict) -> bool:
    if region.get("any"):
        return True
    if alpha2 in region.get("countries", set()):
        return True
    continent = _continent_of(alpha2)
    return continent is not None and continent in region.get("continents", set())


def check_geography(company: Company, icp: ICP) -> dict:
    targets = [t.strip() for t in (icp.target_geographies or []) if t and t.strip()]
    if not targets:
        return {"check": "geography", "result": "pass", "detail": "No geography filter set on the ICP."}
    if not (company.country or "").strip():
        return {"check": "geography", "result": "unknown", "detail": "Company country is missing from the CSV."}

    alpha2 = _canon_country(company.country)
    if alpha2 is None:
        return {
            "check": "geography", "result": "unknown",
            "detail": f"Could not recognize '{company.country}' as a country — routed to review.",
        }

    for target in targets:
        region = REGION_DEFS.get(target.lower())
        if region is not None:
            if _in_region(alpha2, region):
                return {"check": "geography", "result": "pass", "detail": f"{company.country} is within region '{target}'."}
            continue
        if _canon_country(target) == alpha2:
            return {"check": "geography", "result": "pass", "detail": f"{company.country} matches target '{target}'."}
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


def _stable_prompt_prefix(icp: ICP, intelligence: CompanyIntelligence | None) -> str:
    """ICP + sender blocks, identical for every company in an import.

    Prompt-caching design: the user prompt is ordered static-first — this prefix,
    then the per-company prospect block last. Combined with the fixed system prompt,
    every call in an import shares a long identical prefix, so OpenAI's automatic
    prefix caching (1024+ tokens) discounts all calls after the first.
    """
    sender_lines = ["SENDER COMPANY (the company running this campaign):"]
    if intelligence:
        sender_lines += [
            f"- Name: {intelligence.company_name or 'Not provided'}",
            f"- Summary: {intelligence.summary or 'Not provided'}",
            f"- Services: {json.dumps(intelligence.services or [])}",
            f"- Value propositions: {json.dumps(intelligence.value_propositions or [])}",
        ]
    else:
        sender_lines.append("Not available — return an empty solvable_pain_points list.")
    return (
        "ICP (Ideal Customer Profile):\n"
        f"- Target industries: {icp.target_industries or []}\n"
        f"- Fit keywords: {icp.target_keywords or []}\n"
        f"- Campaign objective: {icp.campaign_objective or 'Not specified'}\n\n"
        + "\n".join(sender_lines)
    )


def _to_pain_points(value) -> list[dict]:
    """Validate the LLM's solvable_pain_points; malformed output degrades to []."""
    if not isinstance(value, list):
        return []
    points = []
    for item in value[:4]:
        if isinstance(item, dict) and item.get("pain_point"):
            points.append({
                "pain_point": str(item.get("pain_point") or ""),
                "evidence": str(item.get("evidence") or ""),
                "solved_by": str(item.get("solved_by") or ""),
            })
    return points


def score_company_fit(
    company: Company, icp: ICP, intelligence: CompanyIntelligence | None = None
) -> tuple[float, float, str, list[dict]]:
    """One LLM call -> (industry_match_score, company_fit_score, reasoning, solvable_pain_points)."""
    profile = company.enrichment_profile
    user_prompt = (
        f"{_stable_prompt_prefix(icp, intelligence)}\n\n"
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
        _to_pain_points(data.get("solvable_pain_points")),
    )


def _apply_scoring_result(
    qualification: CompanyQualification, company: Company, checks: list[dict], scoring: tuple | Exception
) -> QualificationStatus:
    """Fold one scoring outcome (or failure) into the qualification row and its checks."""
    if isinstance(scoring, Exception):
        logger.warning("LLM qualification scoring failed for %s: %s", company.name, scoring)
        checks.append({"check": "fit_scoring", "result": "unknown", "detail": "AI scoring failed — routed to review."})
        return QualificationStatus.REVIEW

    industry_score, fit_score, reasoning, pain_points = scoring
    qualification.industry_match_score = industry_score
    qualification.company_fit_score = fit_score
    qualification.qualification_reasoning = reasoning
    qualification.solvable_pain_points = pain_points or None

    average = round((industry_score + fit_score) / 2, 1)
    if average >= QUALIFIED_THRESHOLD:
        status, result = QualificationStatus.APPROVED, "pass"
    else:
        # Low LLM scores never reject — only the deterministic gates can. The user
        # decides review companies via the bulk "Approve companies" action.
        status, result = QualificationStatus.REVIEW, "unknown"

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
    return status


# Big per-company payloads (crawled website text, AI profile, check details). The session
# runs with expire_on_commit=False, so once committed these would otherwise sit in RAM for
# the whole task — expire them right after their wave commits so the memory is reclaimed.
_HEAVY_COMPANY_FIELDS = ["enrichment_content", "enrichment_profile"]


def qualify_import(db: Session, lead_import: LeadImport, icp: ICP) -> dict:
    """Two-stage qualification.

    Stage 1 — deterministic gates for the whole import in one in-memory pass:
    fails/unknowns get their verdict + checks persisted in a single commit (users see
    every cheap rejection within seconds); gate-passers move on with checks in hand.

    Stage 2 — gate-passers in waves of QUALIFY_PARALLELISM: each wave is enriched
    concurrently (shared scraper runtime), then scored with concurrent LLM calls
    (thread pool; the sync OpenAI/Redis clients are thread-safe). All DB writes stay
    on this thread. One commit per wave doubles as the batch commit, and heavy fields
    are expired afterwards so RAM stays flat across arbitrarily large imports.
    """
    repo = CompanyRepository(db)
    # Sender profile is per-import-stable: fetched once, reused for every scoring call.
    intelligence = icp.company_intelligence
    counts = {"approved": 0, "rejected": 0, "review": 0}

    # ---- Stage 1: gates ----
    gate_passed: list[tuple[CompanyQualification, Company, list[dict]]] = []
    for qualification, company in repo.list_for_import(lead_import.id):
        # Skip human decisions AND companies already qualified in a previous run of this
        # import (append mode: only new companies enter the pipeline). A re-run clears
        # qualification_checks/override first, so those are re-processed here.
        if qualification.qualification_override or qualification.qualification_checks is not None:
            counts[qualification.qualification_status.value] = (
                counts.get(qualification.qualification_status.value, 0) + 1
            )
            continue
        checks = [check_geography(company, icp), check_employee_size(company, icp)]
        gate_results = {c["result"] for c in checks}
        if "fail" in gate_results:
            qualification.qualification_status = QualificationStatus.REJECTED
            qualification.qualification_checks = checks
            counts["rejected"] += 1
        elif "unknown" in gate_results:
            qualification.qualification_status = QualificationStatus.REVIEW
            qualification.qualification_checks = checks
            counts["review"] += 1
        else:
            # No checks written yet: a crash before stage 2 leaves these unmarked, so a
            # re-run re-gates them (cheap) instead of skipping them as already done.
            gate_passed.append((qualification, company, checks))
    lead_import.enrichment_total = len(gate_passed)
    lead_import.enrichment_done = 0
    db.commit()  # every gate verdict lands at once, plus the enrichment denominator

    # ---- Stage 2: enrichment + LLM ranking, pipelined waves ----
    # One EnrichmentSession = one shared scraper runtime (httpx pool + Chromium) for
    # the whole import. The loop below overlaps the stages: while wave N's LLM scoring
    # runs on this thread, wave N+1's scrape is already in flight on the session's
    # background thread — so scoring time is hidden behind scraping for every wave
    # except the last. All DB access stays on this thread.
    parallelism = max(1, settings.QUALIFY_PARALLELISM)
    waves = [gate_passed[i:i + parallelism] for i in range(0, len(gate_passed), parallelism)]
    if waves:
        session = EnrichmentSession()
        try:
            previous: list | None = None
            for wave in waves:
                enrichment = session.start_wave(db, [company for _, company, _ in wave])
                if previous is not None:
                    _score_wave(db, previous, icp, intelligence, counts, lead_import, parallelism)
                session.finish_wave(db, enrichment)
                previous = wave
            _score_wave(db, previous, icp, intelligence, counts, lead_import, parallelism)
        finally:
            session.close()

    lead_import.status = ImportStatus.QUALIFIED
    db.commit()
    return counts


def reactivate_rejected(db: Session, lead_import: LeadImport, icp: ICP, company_ids: list[int]) -> dict:
    """Bring gate-rejected companies into the campaign on explicit user override.

    Rejected companies were dropped at Stage 1 (geography/size gate) with no enrichment
    or scores. Reactivation runs the missing Stage-2 work — enrich each company's website,
    then LLM-score industry match + company fit — and flips the verdict to REACTIVATED so
    their leads join prioritization. Unlike the automated pipeline a low score never sends
    them back to review: the user has decided to include them. Processed in waves like
    qualify_import, committing per wave so progress is observable and restart-safe.
    Idempotent: companies no longer REJECTED are skipped.
    """
    repo = CompanyRepository(db)
    intelligence = icp.company_intelligence
    targets: list[tuple[CompanyQualification, Company, list[dict]]] = []
    for company_id in company_ids:
        qualification = repo.qualification_for(lead_import.id, company_id)
        if not qualification or qualification.qualification_status != QualificationStatus.REJECTED:
            continue
        company = db.get(Company, company_id)
        if company is None:
            continue
        # Keep the gate checks that explain the original rejection; scoring appends to them.
        targets.append((qualification, company, list(qualification.qualification_checks or [])))

    if not targets:
        return {"reactivated": 0}

    parallelism = max(1, settings.QUALIFY_PARALLELISM)
    waves = [targets[i:i + parallelism] for i in range(0, len(targets), parallelism)]
    reactivated = 0
    session = EnrichmentSession()
    try:
        previous: list | None = None
        for wave in waves:
            enrichment = session.start_wave(db, [company for _, company, _ in wave])
            if previous is not None:
                reactivated += _score_wave(db, previous, icp, intelligence, None, lead_import, parallelism, force_include=True)
            session.finish_wave(db, enrichment)
            previous = wave
        reactivated += _score_wave(db, previous, icp, intelligence, None, lead_import, parallelism, force_include=True)
    finally:
        session.close()

    return {"reactivated": reactivated}


def _score_wave(
    db: Session,
    wave: list[tuple[CompanyQualification, Company, list[dict]]],
    icp: ICP,
    intelligence: CompanyIntelligence | None,
    counts: dict | None,
    lead_import: LeadImport,
    parallelism: int,
    force_include: bool = False,
) -> int:
    """LLM-score one enriched wave and commit its verdicts (caller's thread only).

    Returns the number of companies processed. force_include (reactivation of rejected
    companies) overrides the scored verdict to REACTIVATED — the scores are still computed
    and stored to feed lead company-fit, but a low average no longer routes to review, and
    the import-level enrichment denominator is left untouched (it belongs to qualify_import).
    """
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = [pool.submit(score_company_fit, company, icp, intelligence) for _, company, _ in wave]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as exc:
                outcomes.append(exc)

    for (qualification, company, checks), outcome in zip(wave, outcomes):
        status = _apply_scoring_result(qualification, company, checks, outcome)
        if force_include:
            status = QualificationStatus.REACTIVATED
            qualification.qualification_override = True
        elif counts is not None:
            counts[status.value] += 1
        qualification.qualification_status = status
        qualification.qualification_checks = checks

    if not force_include:
        lead_import.enrichment_done += len(wave)
    db.commit()  # per-wave commit: progress survives worker restarts
    for _, company, _ in wave:
        db.expire(company, _HEAVY_COMPANY_FIELDS)
    return len(wave)
