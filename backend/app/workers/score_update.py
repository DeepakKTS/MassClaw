"""Celery tasks for evolution scoring: batch recalculation and promotion/demotion."""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def run_score_recalculation() -> dict:
    """Recalculate composite scores and run promotion/demotion.

    Called periodically by Celery beat.
    """
    from app.core.database import db_session_context, init_db
    from app.core.redis import get_redis_manager, init_redis

    init_db()
    await init_redis()

    async with db_session_context() as session:
        redis = get_redis_manager().get_cache_client()
        from app.services.evolution_service import EvolutionService

        service = EvolutionService(session, redis)
        result = await service.promote_demote_agents()
        logger.info(
            "score_update_complete",
            promoted=len(result.get("promoted", [])),
            demoted=len(result.get("demoted", [])),
        )
        return result
