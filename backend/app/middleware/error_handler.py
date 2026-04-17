"""Global exception → envelope middleware.

Uses :mod:`app.core.errors` to build the canonical response shape.
Handles:

- :class:`MassClawError` subclasses — preserves their status code,
  error_code, and any extra attributes.
- :class:`RateLimitError` — adds ``Retry-After`` header and stashes
  ``retry_after`` in the ``extra`` bag.
- Anything else — logs with traceback and returns a 500 envelope
  without leaking the original exception message (avoids stack
  traces in untrusted-client responses).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.errors import envelope
from app.core.logging import correlation_id_var, get_logger
from app.exceptions import MassClawError, PolicyViolationError, RateLimitError

logger = get_logger("error_handler")


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Convert uncaught exceptions into :class:`ErrorEnvelope` JSON responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except MassClawError as e:
            correlation_id = correlation_id_var.get()

            logger.warning(
                "handled_error",
                error_code=e.error_code,
                detail=e.detail,
                status_code=e.status_code,
                path=request.url.path,
            )

            headers: dict[str, str] = {}
            extra: dict[str, object] | None = None
            if isinstance(e, RateLimitError):
                headers["Retry-After"] = str(e.retry_after)
                extra = {"retry_after": e.retry_after}
            elif isinstance(e, PolicyViolationError):
                extra = {"rule_name": e.rule_name} if e.rule_name else None

            return JSONResponse(
                status_code=e.status_code,
                content=envelope(
                    error_code=e.error_code,
                    detail=e.detail,
                    correlation_id=correlation_id,
                    extra=extra,
                ),
                headers=headers,
            )
        except Exception as e:
            correlation_id = correlation_id_var.get()

            logger.exception(
                "unhandled_error",
                error=str(e),
                error_type=type(e).__name__,
                path=request.url.path,
            )

            return JSONResponse(
                status_code=500,
                content=envelope(
                    error_code="INTERNAL_ERROR",
                    detail="An internal error occurred",
                    correlation_id=correlation_id,
                ),
            )
