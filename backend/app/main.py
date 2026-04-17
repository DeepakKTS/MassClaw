from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.config import get_settings
from app.core.database import dispose_db, get_engine, init_db
from app.core.errors import envelope
from app.core.logging import correlation_id_var, get_logger, setup_logging
from app.core.redis import dispose_redis, get_redis_manager, init_redis
from app.embeddings.service import init_embedding_service
from app.middleware.body_size import BodySizeLimitMiddleware
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

logger = get_logger(__name__)


# Map HTTP status → stable error_code string. Keeps the ``HTTPException``
# pathway aligned with the ``MassClawError`` vocabulary without
# forcing every ``raise HTTPException`` site to pass one explicitly.
_STATUS_ERROR_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_FAILED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


def _error_code_for_status(status: int) -> str:
    if status in _STATUS_ERROR_CODES:
        return _STATUS_ERROR_CODES[status]
    if status >= 500:
        return "INTERNAL_ERROR"
    return f"HTTP_{status}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, Any]:
    """Application lifespan: initialize and dispose resources."""
    settings = get_settings()
    setup_logging()

    logger.info("starting_massclaw", version=settings.app_version)

    # Validate production safety constraints
    settings.validate_production_settings()

    # Initialize database
    init_db()
    logger.info("database_initialized", url=settings.database_url.split("@")[-1])

    # Initialize Redis
    redis_manager = await init_redis()
    redis_healthy = await redis_manager.health_check()
    logger.info("redis_initialized", healthy=redis_healthy)

    # Initialize embedding model
    embedding_service = await init_embedding_service()
    logger.info(
        "embedding_service_initialized",
        model=embedding_service.model_name,
        dimensions=embedding_service.dimensions,
        loaded=embedding_service.is_loaded,
    )

    # Pre-warm injection detection bank so the first user request isn't slow
    if embedding_service.is_loaded:
        try:
            from app.safety.injection_detector import InjectionDetector

            detector = InjectionDetector()
            await detector._ensure_injection_bank()
            logger.info("injection_bank_prewarmed")
        except Exception as e:
            logger.warning("injection_bank_prewarm_failed", error=str(e))

    # Initialize tool registry
    from app.tools.registry import get_tool_registry

    tool_registry = get_tool_registry()
    logger.info("tool_registry_initialized", tools=len(tool_registry.list_tools()))

    # Load Phase-1 built-in policy rules. Importing the package runs
    # every rule module's @policy_rule decorator so PolicyRegistry is
    # populated before the first request reaches the scheduler.
    from app.safety.rules import load_builtin_rules

    loaded_rules = load_builtin_rules()
    logger.info("policy_rules_loaded", count=len(loaded_rules), rule_ids=list(loaded_rules))

    # Optionally register this instance with the NANDA Index. Registration
    # happens in the background so a slow or missing Index never blocks
    # startup — stock agents can still discover us via the well-known URL.
    if settings.nanda_index_enabled and settings.nanda_index_register_on_startup:
        import asyncio as _asyncio

        async def _register_with_nanda_index() -> None:
            from app.core.database import db_session_context
            from app.core.redis import get_redis_manager
            from app.services.identity_service import IdentityService

            try:
                redis = get_redis_manager().get_cache_client()
                async with db_session_context() as _session:
                    service = IdentityService(session=_session, redis=redis)
                    await service.register_instance_with_nanda_index()
            except Exception as exc:
                logger.warning("nanda_index_startup_registration_failed", error=str(exc))

        _asyncio.create_task(_register_with_nanda_index())
        logger.info("nanda_index_startup_registration_scheduled")

    # Start the approval janitor — reaps timed-out HITL requests and
    # transitions orphaned workflows to FAILED. Single background task,
    # cancelled on shutdown.
    import asyncio as _asyncio

    from app.safety.approval_janitor import approval_janitor_loop

    approval_janitor_task = _asyncio.create_task(approval_janitor_loop())
    logger.info("approval_janitor_task_scheduled")

    yield

    # Shutdown
    logger.info("shutting_down_massclaw")

    approval_janitor_task.cancel()
    try:
        await approval_janitor_task
    except _asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("approval_janitor_shutdown_error", error=str(exc))

    # Disconnect MCP servers
    from app.protocols.mcp_registry import get_mcp_manager

    await get_mcp_manager().disconnect_all()
    logger.info("mcp_servers_disconnected")

    await dispose_redis()
    await dispose_db()
    logger.info("massclaw_shutdown_complete")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Decentralized operating layer for AI agents — "
        "enabling discovery, trust, memory, coordination, and safe economic collaboration.",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware (outermost first — execution order is bottom-up)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Correlation-ID"],
    )

    # FastAPI exception handlers — these run BEFORE the middleware
    # layer sees the response, so they're the place to catch
    # ``HTTPException`` (which FastAPI raises/catches internally) and
    # Pydantic validation errors. We normalise both to the standard
    # envelope shape so stock agents get one predictable error
    # contract regardless of which layer raised.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(exc.headers or {})
        logger.info(
            "http_exception",
            path=request.url.path,
            status_code=exc.status_code,
            detail=str(exc.detail),
        )
        # Preserve a ``raise HTTPException(detail={"error": ..., ...})``
        # body so endpoint-specific structured errors survive. The
        # envelope still wraps it, but the original dict lands under
        # ``detail`` so existing clients don't break.
        detail_value = exc.detail
        if isinstance(detail_value, dict):
            detail_str = str(detail_value.get("message") or detail_value.get("error") or detail_value)
            extra = {k: v for k, v in detail_value.items() if k not in {"message", "error"}}
            content = envelope(
                error_code=detail_value.get("error") or _error_code_for_status(exc.status_code),
                detail=detail_str,
                correlation_id=correlation_id_var.get(),
                extra=extra or None,
            )
            # Preserve the raw dict under `detail` for legacy clients
            # that parsed the old shape.
            content["detail"] = detail_value
        else:
            content = envelope(
                error_code=_error_code_for_status(exc.status_code),
                detail=str(detail_value) if detail_value is not None else "",
                correlation_id=correlation_id_var.get(),
            )
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        logger.info(
            "validation_error",
            path=request.url.path,
            count=len(errors),
        )
        return JSONResponse(
            status_code=422,
            content=envelope(
                error_code="VALIDATION_ERROR",
                detail="Request validation failed",
                correlation_id=correlation_id_var.get(),
                errors=errors,
            ),
        )

    # Import and mount API router
    from app.api.router import api_router

    app.include_router(api_router, prefix="/api/v1")

    # Mount WebSocket routes (no prefix — WebSocket paths are at root)
    from app.api.websocket import router as ws_router

    app.include_router(ws_router, tags=["WebSocket"])

    # Mount system monitoring routes
    from app.api.system import router as system_router

    app.include_router(system_router, prefix="/system", tags=["System"])

    # Mount .well-known routes at the HTTP root (no prefix). These are
    # discoverable by convention — e.g. /.well-known/agent-facts.json is the
    # NANDA-native discovery surface for this MassClaw node.
    from app.api.well_known import router as well_known_router

    app.include_router(well_known_router)

    @app.get("/ready", tags=["System"])
    async def readiness_check() -> dict[str, Any]:
        """Readiness probe — confirms DB, Redis, and embedding model are ready."""
        ready: dict[str, Any] = {"ready": True}

        # Check database
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
            ready["database"] = "ready"
        except Exception as e:
            ready["database"] = f"not_ready: {e}"
            ready["ready"] = False

        # Check Redis
        try:
            redis_healthy = await get_redis_manager().health_check()
            ready["redis"] = "ready" if redis_healthy else "not_ready"
            if not redis_healthy:
                ready["ready"] = False
        except Exception as e:
            ready["redis"] = f"not_ready: {e}"
            ready["ready"] = False

        # Check embedding model
        try:
            from app.embeddings.service import get_embedding_service

            svc = get_embedding_service()
            ready["embeddings"] = "ready" if svc else "not_ready"
            if not svc:
                ready["ready"] = False
        except Exception:
            ready["embeddings"] = "not_ready"
            ready["ready"] = False

        return ready

    @app.get("/health", tags=["System"])
    async def health_check() -> dict[str, Any]:
        """System health endpoint checking DB and Redis connectivity."""
        health: dict[str, Any] = {"status": "ok", "version": settings.app_version}

        # Check database
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
            health["database"] = "connected"
        except Exception as e:
            health["database"] = f"error: {e}"
            health["status"] = "degraded"

        # Check Redis
        try:
            redis_healthy = await get_redis_manager().health_check()
            health["redis"] = "connected" if redis_healthy else "error"
            if not redis_healthy:
                health["status"] = "degraded"
        except Exception as e:
            health["redis"] = f"error: {e}"
            health["status"] = "degraded"

        return health

    return app


# The app instance — used by uvicorn
app = create_app()
