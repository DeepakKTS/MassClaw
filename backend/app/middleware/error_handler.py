from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import correlation_id_var, get_logger
from app.exceptions import MassClawError, RateLimitError

logger = get_logger("error_handler")


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Global exception handler that converts exceptions to structured JSON responses."""

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
            if isinstance(e, RateLimitError):
                headers["Retry-After"] = str(e.retry_after)

            return JSONResponse(
                status_code=e.status_code,
                content={
                    "error_code": e.error_code,
                    "detail": e.detail,
                    "correlation_id": correlation_id,
                },
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
                content={
                    "error_code": "INTERNAL_ERROR",
                    "detail": "An internal error occurred",
                    "correlation_id": correlation_id,
                },
            )
