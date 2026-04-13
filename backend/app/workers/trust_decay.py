"""Celery task for periodic trust score decay on inactive agents."""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def run_trust_decay() -> int:
    """Decay trust scores for agents with no recent activity.

    Called periodically by Celery beat.
    Returns count of agents whose trust was decayed.
    """
    from app.core.database import db_session_context, init_db
    from app.core.redis import get_redis_manager, init_redis

    init_db()
    await init_redis()

    async with db_session_context() as session:
        redis = get_redis_manager().get_cache_client()
        from app.services.trust_service import TrustService

        service = TrustService(session, redis)
        decayed = await service.decay_all_scores()
        logger.info("trust_decay_complete", decayed_agents=decayed)
        return decayed
