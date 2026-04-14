from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.database import dispose_db, get_engine, init_db
from app.core.logging import get_logger, setup_logging
from app.core.redis import dispose_redis, get_redis_manager, init_redis
from app.embeddings.service import init_embedding_service
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, Any]:
    """Application lifespan: initialize and dispose resources."""
    settings = get_settings()
    setup_logging()

    logger.info("starting_massclaw", version=settings.app_version)

    # Validate production safety constraints
    settings.validate_production_settings()

    # Initialize database
    engine = init_db()
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
    )

    yield

    # Shutdown
    logger.info("shutting_down_massclaw")
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

    # Import and mount API router
    from app.api.router import api_router

    app.include_router(api_router, prefix="/api/v1")

    # Mount WebSocket routes (no prefix — WebSocket paths are at root)
    from app.api.websocket import router as ws_router

    app.include_router(ws_router, tags=["WebSocket"])

    # Mount system monitoring routes
    from app.api.system import router as system_router

    app.include_router(system_router, prefix="/system", tags=["System"])

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
