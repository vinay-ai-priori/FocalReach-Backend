from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Product(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None


class CaseStudySnippet(BaseModel):
    customer: str | None = None
    summary: str | None = None
    url: str | None = None


class Offering(BaseModel):
    products: list[Product] = Field(default_factory=list)
    pricing_model_hint: str | None = None
    target_customer_hint: str | None = None
    key_features: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)


class IcpSignals(BaseModel):
    industries_served: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    customer_logos: list[str] = Field(default_factory=list)
    case_study_snippets: list[CaseStudySnippet] = Field(default_factory=list)
    certifications_compliance: list[str] = Field(default_factory=list)


class Person(BaseModel):
    name: str | None = None
    title: str | None = None
    bio_snippet: str | None = None
    linkedin_url: str | None = None


class NewsItem(BaseModel):
    title: str | None = None
    date: datetime | None = None
    summary: str | None = None
    url: str | None = None
    category: str | None = None  # funding | partnership | award | product_update | event | press_release | blog


class SocialProof(BaseModel):
    testimonials: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    press_mentions: list[str] = Field(default_factory=list)


class TechSignals(BaseModel):
    detected_tools: list[str] = Field(default_factory=list)


class GrowthSignals(BaseModel):
    """Hiring/growth evidence extracted from the careers page."""

    roles_hiring: list[str] = Field(default_factory=list)
    tech_stack_mentions: list[str] = Field(default_factory=list)


class RawPage(BaseModel):
    url: str
    page_type: str
    extracted_text: str
    fetched_via: str  # "httpx" | "playwright" | "feed"
    extracted_at: datetime


class LlmUsage(BaseModel):
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    duration_ms: int = 0


class ScrapeStats(BaseModel):
    pages_found: int = 0
    pages_scraped: int = 0
    fallback_used_count: int = 0
    duration_ms: int = 0
    truncated_by_budget: bool = False
    stage_timings_ms: dict[str, int] = Field(default_factory=dict)
    llm_usage: LlmUsage | None = None


class ScrapeResult(BaseModel):
    domain: str
    scraped_at: datetime
    offering: Offering = Field(default_factory=Offering)
    icp_signals: IcpSignals = Field(default_factory=IcpSignals)
    people: list[Person] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    social_proof: SocialProof = Field(default_factory=SocialProof)
    tech_signals: TechSignals = Field(default_factory=TechSignals)
    growth_signals: GrowthSignals = Field(default_factory=GrowthSignals)
    stats: ScrapeStats = Field(default_factory=ScrapeStats)
