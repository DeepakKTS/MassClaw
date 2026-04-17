from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "massclaw",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.workers.celery_app.health_check_all": {"queue": "health"},
        "app.workers.celery_app.decay_trust_scores": {"queue": "trust"},
        "app.workers.celery_app.garbage_collect_memory": {"queue": "memory"},
        "app.workers.celery_app.recalculate_scores": {"queue": "workflow"},
        "app.workers.celery_app.crdt_gossip_tick": {"queue": "crdt_sync"},
    },
    beat_schedule={
        "health-check-every-60s": {
            "task": "app.workers.celery_app.health_check_all",
            "schedule": 60.0,
        },
        "trust-decay-every-24h": {
            "task": "app.workers.celery_app.decay_trust_scores",
            "schedule": 86400.0,
        },
        "memory-gc-every-6h": {
            "task": "app.workers.celery_app.garbage_collect_memory",
            "schedule": 21600.0,
        },
        "score-recalc-every-12h": {
            "task": "app.workers.celery_app.recalculate_scores",
            "schedule": 43200.0,
        },
        "crdt-gossip-tick": {
            "task": "app.workers.celery_app.crdt_gossip_tick",
            "schedule": settings.gossip_interval_seconds,
        },
    },
)

# Define tasks as sync wrappers around async functions
import asyncio


@celery_app.task(
    name="app.workers.celery_app.health_check_all",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    retry_jitter=True,
    acks_late=True,
)
def health_check_all():
    """Run health checks on all agents."""
    from app.workers.health_check import run_health_checks

    return asyncio.run(run_health_checks())


@celery_app.task(
    name="app.workers.celery_app.decay_trust_scores",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    retry_jitter=True,
    acks_late=True,
)
def decay_trust_scores():
    """Decay trust scores for inactive agents."""
    from app.workers.trust_decay import run_trust_decay

    return asyncio.run(run_trust_decay())


@celery_app.task(
    name="app.workers.celery_app.garbage_collect_memory",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    retry_jitter=True,
    acks_late=True,
)
def garbage_collect_memory():
    """Garbage collect expired/low-quality memory records."""
    from app.workers.memory_gc import run_memory_gc

    return asyncio.run(run_memory_gc())


@celery_app.task(
    name="app.workers.celery_app.recalculate_scores",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    retry_jitter=True,
    acks_late=True,
)
def recalculate_scores():
    """Recalculate agent scores and run promotion/demotion."""
    from app.workers.score_update import run_score_recalculation

    return asyncio.run(run_score_recalculation())


@celery_app.task(
    name="app.workers.celery_app.crdt_gossip_tick",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=2,
    retry_jitter=True,
    acks_late=True,
)
def crdt_gossip_tick():
    """Run one CRDT gossip round — reconciles with 2 random peers."""
    from app.workers.crdt_gossip import run_crdt_gossip_sync

    return run_crdt_gossip_sync()
