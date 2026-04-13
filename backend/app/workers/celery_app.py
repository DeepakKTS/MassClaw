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
    },
)

# Define tasks as sync wrappers around async functions
import asyncio

@celery_app.task(name="app.workers.celery_app.health_check_all")
def health_check_all():
    """Run health checks on all agents."""
    from app.workers.health_check import run_health_checks
    return asyncio.run(run_health_checks())

@celery_app.task(name="app.workers.celery_app.decay_trust_scores")
def decay_trust_scores():
    """Decay trust scores for inactive agents."""
    from app.workers.trust_decay import run_trust_decay
    return asyncio.run(run_trust_decay())

@celery_app.task(name="app.workers.celery_app.garbage_collect_memory")
def garbage_collect_memory():
    """Garbage collect expired/low-quality memory records."""
    from app.workers.memory_gc import run_memory_gc
    return asyncio.run(run_memory_gc())

@celery_app.task(name="app.workers.celery_app.recalculate_scores")
def recalculate_scores():
    """Recalculate agent scores and run promotion/demotion."""
    from app.workers.score_update import run_score_recalculation
    return asyncio.run(run_score_recalculation())
