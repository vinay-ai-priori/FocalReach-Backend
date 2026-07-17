"""
Narrower pydantic model the LLM is asked to fill in. Only the semantic
fields that need reading comprehension over raw page text (products,
ICP signals, people, social proof) go here. Deterministic fields
(tech_signals, news, stats) are never touched by the LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from scraper.schema.models import CaseStudySnippet, IcpSignals, Offering, Person


class EnrichmentResult(BaseModel):
    offering: Offering = Field(default_factory=Offering)
    icp_signals: IcpSignals = Field(default_factory=IcpSignals)
    people: list[Person] = Field(default_factory=list)
    social_proof_testimonials: list[str] = Field(default_factory=list)
    social_proof_awards: list[str] = Field(default_factory=list)
    social_proof_press_mentions: list[str] = Field(default_factory=list)


__all__ = ["EnrichmentResult", "CaseStudySnippet"]
