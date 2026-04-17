"""Body-size limit middleware.

Rejects requests whose ``Content-Length`` exceeds the configured
maximum with a structured 413 response. Streaming requests without a
``Content-Length`` pass through untouched — FastAPI/Starlette still
caps in-memory buffering, and our use cases don't need chunked
uploads today.

Why a dedicated middleware: uvicorn has a ``--limit-max-requests`` but
no built-in body size cap. A JSON payload big enough to OOM the
process is a trivial DoS surface otherwise.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.core.errors import envelope
from app.core.logging import correlation_id_var, get_logger

logger = get_logger("body_size")


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject bodies larger than ``settings.max_request_body_bytes``."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        limit = int(getattr(settings, "max_request_body_bytes", 1_048_576))  # default 1 MiB
        content_length_header = request.headers.get("content-length")

        if content_length_header is not None:
            try:
                content_length = int(content_length_header)
            except ValueError:
                content_length = None
            if content_length is not None and content_length > limit:
                logger.info(
                    "request_body_too_large",
                    path=request.url.path,
                    content_length=content_length,
                    limit=limit,
                )
                return JSONResponse(
                    status_code=413,
                    content=envelope(
                        error_code="PAYLOAD_TOO_LARGE",
                        detail=f"Request body exceeds {limit} bytes ({content_length} bytes received).",
                        correlation_id=correlation_id_var.get(),
                        extra={"limit_bytes": limit, "received_bytes": content_length},
                    ),
                )

        return await call_next(request)
