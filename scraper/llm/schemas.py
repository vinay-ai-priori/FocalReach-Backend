"""
Narrower pydantic model the LLM is asked to fill in. Only the semantic
fields that need reading comprehension over raw page text (products,
ICP signals, people) go here. Deterministic fields (tech_signals, news,
stats) are never touched by the LLM.

The slim Llm* variants deliberately omit fields whose source pages are
out of crawl scope or whose values arrive from the lead CSV
(pricing_model_hint, customer_logos, certifications_compliance,
bio_snippet, social proof) — a smaller output schema means shorter
prompts and completions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from scraper.schema.models import CaseStudySnippet


class LlmProduct(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None


class LlmOffering(BaseModel):
    products: list[LlmProduct] = Field(default_factory=list)
    target_customer_hint: str | None = None
    key_features: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)


class LlmIcpSignals(BaseModel):
    industries_served: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    case_study_snippets: list[CaseStudySnippet] = Field(default_factory=list)  # max 3, enforced in prompt


class LlmPerson(BaseModel):
    name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None


class LlmGrowthSignals(BaseModel):
    roles_hiring: list[str] = Field(default_factory=list)
    tech_stack_mentions: list[str] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    offering: LlmOffering = Field(default_factory=LlmOffering)
    icp_signals: LlmIcpSignals = Field(default_factory=LlmIcpSignals)
    people: list[LlmPerson] = Field(default_factory=list)
    growth_signals: LlmGrowthSignals = Field(default_factory=LlmGrowthSignals)


__all__ = ["EnrichmentResult", "CaseStudySnippet", "LlmOffering", "LlmIcpSignals", "LlmPerson", "LlmGrowthSignals"]
