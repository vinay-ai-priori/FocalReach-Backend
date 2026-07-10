"""Canonical field catalog for lead CSV imports.

Incoming CSVs have unpredictable column names, so each canonical field carries
synonyms used for fuzzy matching, whether it is mandatory, which stage needs it,
and the concrete consequence shown to the user when it is missing."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    synonyms: tuple[str, ...]
    required_for: str | None = None  # company_qualification | lead_qualification | outreach
    is_mandatory: bool = False
    consequence_if_missing: str = ""
    aliases_exact_priority: tuple[str, ...] = field(default_factory=tuple)


FIELD_DEFINITIONS: list[FieldDef] = [
    # ---------- Company qualification ----------
    FieldDef(
        key="company_name",
        label="Company Name",
        synonyms=("company name", "company name - cleaned", "company", "account name", "organization"),
        required_for="company_qualification",
        is_mandatory=True,
        consequence_if_missing="Rows cannot be grouped into companies. The import cannot continue without this field.",
        aliases_exact_priority=("company name - cleaned", "company name"),
    ),
    FieldDef(
        key="company_website",
        label="Company Website",
        synonyms=("website", "company website domain", "company website", "domain", "web site", "url"),
        required_for="company_qualification",
        is_mandatory=True,
        consequence_if_missing="Companies cannot be enriched or crawled for email personalization; duplicate companies may not be merged correctly.",
    ),
    FieldDef(
        key="company_industry",
        label="Company Industry",
        synonyms=("company industry", "industry", "sector", "vertical"),
        required_for="company_qualification",
        is_mandatory=True,
        consequence_if_missing="Industry match cannot be evaluated. All companies will fall to the Review bucket instead of being auto-approved or rejected.",
    ),
    FieldDef(
        key="company_employee_count",
        label="Company Employee Count",
        synonyms=("company staff count", "employee count", "employees", "headcount", "staff count", "number of employees"),
        required_for="company_qualification",
        is_mandatory=True,
        consequence_if_missing="Employee-size match cannot be evaluated. Companies without a size signal will be routed to Review.",
    ),
    FieldDef(
        key="company_employee_range",
        label="Company Employee Range",
        synonyms=("company staff count range", "employee range", "company size", "size range"),
        required_for="company_qualification",
        consequence_if_missing="Used as a fallback when exact employee count is missing.",
    ),
    FieldDef(
        key="company_country",
        label="Company Country",
        synonyms=("company country", "company country (alpha 2)", "company country (alpha 3)", "country", "hq country"),
        required_for="company_qualification",
        is_mandatory=True,
        consequence_if_missing="Geography match cannot be evaluated. Companies will be routed to Review for a human decision.",
    ),
    FieldDef(
        key="company_city",
        label="Company City",
        synonyms=("company city", "city", "hq city"),
        required_for="company_qualification",
        consequence_if_missing="Location detail on the company profile will be limited to country level.",
    ),
    FieldDef(
        key="company_state",
        label="Company State",
        synonyms=("company state", "company state abbr", "state", "region"),
        required_for="company_qualification",
        consequence_if_missing="Location detail on the company profile will be limited.",
    ),
    FieldDef(
        key="company_revenue",
        label="Company Annual Revenue",
        synonyms=("company annual revenue", "annual revenue", "revenue", "company revenue range", "revenue range"),
        required_for="company_qualification",
        consequence_if_missing="Revenue will not appear on company profiles and cannot support size assessment during review.",
    ),
    FieldDef(
        key="company_description",
        label="Company Description",
        synonyms=("company description", "description", "about", "company overview"),
        required_for="company_qualification",
        consequence_if_missing="Reviewers will see no company summary; email personalization will rely entirely on website crawling.",
    ),
    FieldDef(
        key="company_linkedin",
        label="Company LinkedIn URL",
        synonyms=("company li profile url", "company linkedin", "company linkedin url"),
        # Optional and silent: captured when present, but never warned about when missing.
        consequence_if_missing="",
    ),
    # ---------- Lead qualification ----------
    FieldDef(
        key="full_name",
        label="Contact Full Name",
        synonyms=("contact full name", "full name", "name", "prospect name"),
        required_for="lead_qualification",
        is_mandatory=True,
        consequence_if_missing="Leads cannot be identified. Rows without a name are skipped.",
    ),
    FieldDef(
        key="title",
        label="Job Title / Role",
        synonyms=("title", "job title", "role", "position", "designation"),
        required_for="lead_qualification",
        is_mandatory=True,
        consequence_if_missing="Role score cannot be computed. Affected leads will score 0 on the role dimension and will likely land in Nurture or Deprioritized.",
    ),
    FieldDef(
        key="seniority",
        label="Seniority",
        synonyms=("seniority", "seniority level", "job level"),
        required_for="lead_qualification",
        is_mandatory=True,
        consequence_if_missing="Seniority will be inferred from the job title where possible; inference is less accurate than explicit data.",
    ),
    FieldDef(
        key="department",
        label="Department",
        synonyms=("department", "function", "team"),
        required_for="lead_qualification",
        is_mandatory=True,
        consequence_if_missing="Department relevance cannot boost role scoring; borderline leads may be under-scored.",
    ),
    FieldDef(
        key="email",
        label="Primary Email",
        synonyms=("primary email", "contact email", "email", "email 1", "work email", "email address"),
        required_for="outreach",
        is_mandatory=True,
        consequence_if_missing="Leads without an email cannot receive outreach. They will be scored but excluded from the email drafting step.",
        aliases_exact_priority=("primary email", "contact email", "email 1"),
    ),
    FieldDef(
        key="time_in_role",
        label="Time in Role",
        synonyms=("time in role", "role tenure", "tenure in role", "years in role"),
        required_for="lead_qualification",
        is_mandatory=True,
        consequence_if_missing="Fit score loses the role-tenure signal and will use a neutral default (50/100) for that component.",
    ),
    FieldDef(
        key="time_at_company",
        label="Time at Company",
        synonyms=("time at company", "company tenure", "tenure at company", "years at company"),
        required_for="lead_qualification",
        is_mandatory=True,
        consequence_if_missing="Fit score loses the company-tenure signal and will use a neutral default (50/100) for that component.",
    ),
    FieldDef(
        key="years_experience",
        label="Total Years of Experience",
        synonyms=("years of experience", "total years experience", "total experience", "experience", "years experience"),
        required_for="lead_qualification",
        consequence_if_missing="Signal score will estimate total experience from role tenure and flag it as estimated; if tenure is also missing, that sub-score is 0.",
    ),
    FieldDef(
        key="contact_country",
        label="Contact Country",
        synonyms=("contact country", "contact country (alpha 2)", "contact location - country", "country"),
        required_for="lead_qualification",
        consequence_if_missing="Lead-level geography will fall back to the company country.",
    ),
    FieldDef(
        key="linkedin_url",
        label="Contact LinkedIn URL",
        synonyms=("contact li profile url", "linkedin", "linkedin url", "li profile"),
        consequence_if_missing="LinkedIn links will not be available on lead cards.",
    ),
    FieldDef(
        key="phone",
        label="Contact Phone",
        synonyms=("contact phone", "contact phone 1", "phone", "contact mobile phone", "mobile phone"),
        # Optional and silent: reserved for a planned future feature, never warned about when missing.
        consequence_if_missing="",
    ),
]

MANDATORY_FIELDS = [f for f in FIELD_DEFINITIONS if f.is_mandatory]
FIELDS_BY_KEY = {f.key: f for f in FIELD_DEFINITIONS}
