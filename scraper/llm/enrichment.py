from __future__ import annotations

import asyncio
import logging
import time

from openai import AsyncOpenAI

from scraper.config.settings import ScraperSettings
from scraper.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from scraper.llm.schemas import EnrichmentResult
from scraper.schema.models import LlmUsage, RawPage

logger = logging.getLogger(__name__)

# USD per 1M tokens. Verify against current OpenAI pricing before relying
# on this for billing-grade reporting -- these are point-in-time figures.
_PRICING_PER_1M_TOKENS = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = _PRICING_PER_1M_TOKENS.get(model)
    if not rates:
        return 0.0
    return (prompt_tokens / 1_000_000) * rates["input"] + (completion_tokens / 1_000_000) * rates["output"]


async def enrich_with_llm(
    domain: str,
    raw_pages: list[RawPage],
    settings: ScraperSettings,
    api_key: str,
) -> tuple[EnrichmentResult | None, LlmUsage | None]:
    """
    Single structured-output call to gpt-4o-mini over the bucketed page
    text. Returns (None, None) on any failure/timeout so the pipeline can
    fall back to deterministic-only output rather than fail the whole scrape.
    """
    if not raw_pages:
        return None, None

    client = AsyncOpenAI(api_key=api_key)
    user_prompt = build_user_prompt(domain, raw_pages, settings.llm_max_input_chars)
    start = time.monotonic()

    try:
        completion = await asyncio.wait_for(
            client.beta.chat.completions.parse(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=EnrichmentResult,
            ),
            timeout=settings.llm_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM enrichment timed out for domain=%s", domain)
        return None, None
    except Exception:
        logger.exception("LLM enrichment failed for domain=%s", domain)
        return None, None
    finally:
        await client.close()

    parsed = completion.choices[0].message.parsed
    usage = completion.usage
    llm_usage = LlmUsage(
        model=settings.llm_model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
        estimated_cost_usd=_estimate_cost(
            settings.llm_model,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        ),
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    return parsed, llm_usage
