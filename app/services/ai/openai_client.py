"""Thin OpenAI wrapper with Redis response caching.
Every AI call in the app goes through `cached_completion` so identical prompts never
hit the API twice within the cache TTL."""

import hashlib
import json

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

_client: OpenAI | None = None
CACHE_PREFIX = "ai_response:"


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise ExternalServiceError("OPENAI_API_KEY is not configured.")
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _cache_key(system: str, user: str, model: str) -> str:
    digest = hashlib.sha256(f"{model}|{system}|{user}".encode()).hexdigest()
    return f"{CACHE_PREFIX}{digest}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_openai(system: str, user: str, model: str, json_mode: bool) -> str:
    response = _get_client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        response_format={"type": "json_object"} if json_mode else {"type": "text"},
    )
    return response.choices[0].message.content or ""


def cached_completion(system: str, user: str, *, json_mode: bool = True) -> tuple[str, bool]:
    """Returns (content, was_cached)."""
    model = settings.OPENAI_MODEL
    key = _cache_key(system, user, model)

    try:
        cached = get_redis().get(key)
        if cached:
            logger.info("AI cache hit (%s...)", key[:24])
            return cached, True
    except Exception as exc:
        logger.warning("Redis unavailable for AI cache read: %s", exc)

    content = _call_openai(system, user, model, json_mode)

    try:
        get_redis().setex(key, settings.AI_CACHE_TTL_SECONDS, content)
    except Exception as exc:
        logger.warning("Redis unavailable for AI cache write: %s", exc)
    return content, False


def cached_json_completion(system: str, user: str) -> tuple[dict, bool]:
    content, was_cached = cached_completion(system, user, json_mode=True)
    try:
        return json.loads(content), was_cached
    except json.JSONDecodeError as exc:
        raise ExternalServiceError("AI returned malformed JSON.") from exc
