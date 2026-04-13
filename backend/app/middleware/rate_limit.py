from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.core.redis import get_redis_manager

# Paths exempt from rate limiting
EXEMPT_PATHS = {"/health", "/metrics", "/api/v1/workflows/{id}/stream"}

# Path prefixes exempt from rate limiting (polling endpoints)
EXEMPT_PREFIXES = ("/api/v1/workflows/", "/api/v1/tasks/", "/api/v1/wallet/")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed sliding window rate limiter."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Exempt polling endpoints (GET on workflow/task/wallet status)
        if request.method == "GET" and any(request.url.path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        settings = get_settings()

        # Rate limit key: by IP (or API key if present)
        client_ip = request.client.host if request.client else "unknown"
        api_key = request.headers.get("X-API-Key")
        key_id = api_key[:16] if api_key else client_ip
        redis_key = f"rate_limit:{key_id}"

        try:
            redis = get_redis_manager().get_rate_limit_client()
            now = time.time()
            window_start = now - settings.rate_limit_window_seconds

            pipe = redis.pipeline()
            # Remove old entries outside the window
            pipe.zremrangebyscore(redis_key, 0, window_start)
            # Count current entries in window
            pipe.zcard(redis_key)
            # Add current request
            pipe.zadd(redis_key, {str(now): now})
            # Set TTL on the key
            pipe.expire(redis_key, settings.rate_limit_window_seconds)
            results = await pipe.execute()

            request_count = results[1]

            if request_count >= settings.rate_limit_requests:
                retry_after = settings.rate_limit_window_seconds
                return JSONResponse(
                    status_code=429,
                    content={
                        "error_code": "RATE_LIMITED",
                        "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        except Exception:
            # If Redis is down, allow the request through (fail-open)
            pass

        response = await call_next(request)

        return response
