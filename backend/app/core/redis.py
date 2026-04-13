from __future__ import annotations

import redis.asyncio as aioredis

from app.config import get_settings


class RedisManager:
    """Manages multiple Redis connection pools for different purposes."""

    def __init__(self) -> None:
        self._pools: dict[int, aioredis.ConnectionPool] = {}
        self._clients: dict[int, aioredis.Redis] = {}

    async def initialize(self) -> None:
        """Create connection pools for each Redis database."""
        settings = get_settings()
        # DB 0: General cache
        # DB 1: Pub/sub events
        # DB 2: Rate limiting
        # DB 3: Celery (managed by Celery, not us)
        for db_number in (0, 1, 2):
            pool = aioredis.ConnectionPool.from_url(
                settings.redis_url,
                db=db_number,
                max_connections=50,
                decode_responses=True,
            )
            self._pools[db_number] = pool
            self._clients[db_number] = aioredis.Redis(connection_pool=pool)

    def get_cache_client(self) -> aioredis.Redis:
        return self._clients[0]

    def get_pubsub_client(self) -> aioredis.Redis:
        return self._clients[1]

    def get_rate_limit_client(self) -> aioredis.Redis:
        return self._clients[2]

    async def health_check(self) -> bool:
        """Check if Redis is reachable."""
        try:
            client = self.get_cache_client()
            return await client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        """Close all connection pools."""
        for client in self._clients.values():
            await client.aclose()
        for pool in self._pools.values():
            await pool.aclose()
        self._clients.clear()
        self._pools.clear()


# Module-level singleton
_redis_manager: RedisManager | None = None


def get_redis_manager() -> RedisManager:
    if _redis_manager is None:
        raise RuntimeError("Redis manager not initialized. Call init_redis() first.")
    return _redis_manager


async def init_redis() -> RedisManager:
    global _redis_manager
    _redis_manager = RedisManager()
    await _redis_manager.initialize()
    return _redis_manager


async def dispose_redis() -> None:
    global _redis_manager
    if _redis_manager is not None:
        await _redis_manager.close()
        _redis_manager = None


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency that returns the cache Redis client."""
    return get_redis_manager().get_cache_client()
