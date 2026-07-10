"""Per-client rate limiting for all API routes, backed by Redis.

Fixed one-minute windows keyed by client IP (+ a stricter bucket for auth
endpoints, which are brute-force targets). Fails open if Redis is unavailable
so an infra hiccup never takes the API down with it.
"""

import logging

import redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if path == "/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        is_auth = path.startswith(f"{settings.API_V1_PREFIX}/auth")
        limit = settings.RATE_LIMIT_AUTH_PER_MINUTE if is_auth else settings.RATE_LIMIT_PER_MINUTE
        bucket = "auth" if is_auth else "api"
        key = f"ratelimit:{bucket}:{client_ip}"

        try:
            r = get_redis()
            count = r.incr(key)
            if count == 1:
                r.expire(key, WINDOW_SECONDS)
            remaining = max(0, limit - count)
            if count > limit:
                retry_after = r.ttl(key)
                if retry_after < 0:
                    retry_after = WINDOW_SECONDS
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests — please slow down and retry shortly."},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
        except redis.RedisError:
            logger.warning("Rate limiter unavailable (Redis error); allowing request")
            return await call_next(request)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
