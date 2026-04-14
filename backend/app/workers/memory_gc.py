from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def run_memory_gc() -> dict:
    """Garbage collect expired and low-quality memory records."""
    from app.core.database import db_session_context, init_db
    from app.core.redis import get_redis_manager, init_redis

    logger.info("memory_gc_started")
    try:
        init_db()
        await init_redis()

        async with db_session_context() as session:
            redis = get_redis_manager().get_cache_client()
            from app.services.memory_service import MemoryService

            service = MemoryService(session, redis)
            result = await service.garbage_collect()
            logger.info("memory_gc_complete", **result)
            return result
    except Exception:
        logger.exception("memory_gc_failed")
        raise
