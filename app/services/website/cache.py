"""Redis cache for crawled website content keyed by domain.
The WebsiteAnalysis DB row is the durable cache; Redis avoids re-reading large text blobs."""

import json

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

KEY_PREFIX = "website_content:"


def get_cached_content(domain: str) -> dict | None:
    try:
        raw = get_redis().get(f"{KEY_PREFIX}{domain}")
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Redis unavailable for cache read: %s", exc)
        return None


def set_cached_content(domain: str, payload: dict) -> None:
    try:
        get_redis().setex(f"{KEY_PREFIX}{domain}", settings.WEBSITE_CACHE_TTL_SECONDS, json.dumps(payload))
    except Exception as exc:
        logger.warning("Redis unavailable for cache write: %s", exc)
