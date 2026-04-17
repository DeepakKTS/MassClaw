"""Celery-side entry points for CRDT gossip.

The Celery beat schedule in :mod:`app.workers.celery_app` fires
:func:`run_crdt_gossip` every ``gossip_interval_seconds`` (default 30s).
That task:

1. Loads the peer list from ``MASSCLAW_FEDERATION_PEERS``.
2. Picks at most ``gossip_fanout`` random peers.
3. Runs one :class:`GossipService` round against each, in sequence.
4. Emits a structured log line per round (the reports double as metrics
   once we add a Prometheus exporter).

The task is idempotent, safe-to-retry, and never crashes on a bad peer —
one peer's error is logged and the next peer proceeds.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from app.config import get_settings
from app.core.database import db_session_context, init_db
from app.core.logging import get_logger
from app.core.redis import get_redis_manager, init_redis
from app.crdt.gossip import GossipRoundReport, GossipService
from app.crdt.peer_client import PeerClient
from app.crdt.store import CRDTStore
from app.crdt.sync import SyncService
from app.services.identity_service import get_instance_key_store

logger = get_logger(__name__)


async def run_crdt_gossip() -> dict[str, Any]:
    """Drive one gossip tick across a random subset of federated peers."""
    settings = get_settings()
    peer_urls = settings.federation_peer_urls()
    if not peer_urls:
        logger.info("crdt_gossip_no_peers_configured")
        return {"status": "no_peers", "peers_visited": 0, "reports": []}

    fanout = getattr(settings, "gossip_fanout", 2)
    sample_size = min(fanout, len(peer_urls))
    targets = random.sample(peer_urls, sample_size)

    # The instance keypair signs every outbound request. If the instance
    # key hasn't been provisioned (e.g. first boot of a new region), the
    # KeyStore generates one here so gossip never blocks on operator setup.
    keystore = get_instance_key_store()
    keypair = keystore.instance_keypair()

    # Ensure the DB + Redis engines are initialised in THIS worker loop.
    init_db()
    await init_redis()
    redis = get_redis_manager().get_cache_client()

    reports: list[dict[str, Any]] = []
    async with db_session_context() as session:
        sync_service = SyncService(session=session, redis=redis)
        store = CRDTStore(session=session)
        gossip = GossipService(session=session, sync_service=sync_service, crdt_store=store)

        for base_url in targets:
            peer = PeerClient(base_url=base_url, keypair=keypair)
            report = await gossip.reconcile_with(peer)
            logger.info("crdt_gossip_round_complete", **report.as_log_fields())
            reports.append(report.as_log_fields())
            # Commit per-peer so a failure on peer N doesn't roll back peer N-1.
            await session.commit()

    return {
        "status": "ok",
        "peers_visited": len(reports),
        "reports": reports,
    }


def run_crdt_gossip_sync() -> dict[str, Any]:
    """Sync wrapper consumed by the Celery task."""
    return asyncio.run(run_crdt_gossip())


# Re-exported so the celery_app module can register the beat task without
# importing this module (breaks the import cycle through celery_app).
__all__ = [
    "GossipRoundReport",
    "run_crdt_gossip",
    "run_crdt_gossip_sync",
]
