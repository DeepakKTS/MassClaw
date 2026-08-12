"""Counters for the semantic result cache.

A cache hit is invisible in the workflow record: the reservation settles at
zero credits, latency reads 0.5ms, and the task looks like one that was simply
cheap. That makes "is the cache working?" and "why did spend drop?"
unanswerable from the data — the only trace is a log line. These counters give
``/system/metrics`` the four numbers that actually answer it.

Deliberately Redis, not in-process: the API process serving /system/metrics is
not the process that runs the scheduler.

Every function here is best-effort. Telemetry must never fail a workflow, so
callers get a silent no-op if Redis is unreachable rather than an exception on
the cache lookup path.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger(__name__)

_PREFIX = "metrics:semantic_cache"

#: Outcome → Redis key. ``rejected`` is a subset of ``misses``: an entry that
#: matched on similarity but failed the poison check still costs the caller an
#: LLM call, so it counts as a miss, while the separate tally shows *why*.
CACHE_METRIC_KEYS: dict[str, str] = {
    "hits": f"{_PREFIX}:hits",
    "misses": f"{_PREFIX}:misses",
    "stores": f"{_PREFIX}:stores",
    "rejected": f"{_PREFIX}:rejected",
}


async def record_semantic_cache_event(redis: aioredis.Redis, *outcomes: str) -> None:
    """Increment one or more counters. Never raises."""
    for outcome in outcomes:
        key = CACHE_METRIC_KEYS.get(outcome)
        if key is None:  # pragma: no cover — programming error, not runtime state
            raise KeyError(f"unknown semantic cache outcome {outcome!r}")
        try:
            await redis.incr(key)
        except Exception as e:
            logger.debug("semantic_cache_metric_failed", outcome=outcome, error=str(e))
            return


async def semantic_cache_snapshot(redis: aioredis.Redis) -> dict[str, float]:
    """Return the counters plus the derived hit rate.

    Missing keys read as zero, so a fresh instance (or one whose Redis was
    flushed) reports zeros rather than failing the metrics endpoint.
    """
    names = list(CACHE_METRIC_KEYS)
    try:
        raw = await redis.mget([CACHE_METRIC_KEYS[name] for name in names])
    except Exception as e:
        logger.debug("semantic_cache_snapshot_failed", error=str(e))
        raw = [None] * len(names)

    counts: dict[str, float] = {}
    for name, value in zip(names, raw, strict=True):
        try:
            counts[name] = int(value) if value is not None else 0
        except (TypeError, ValueError):
            counts[name] = 0

    lookups = counts["hits"] + counts["misses"]
    counts["hit_rate"] = round(counts["hits"] / lookups, 4) if lookups else 0.0
    return counts
