#!/bin/sh
# Worker entrypoint — runs Celery beat + worker in one container.
#
# Combining the two saves six containers in the 3-node demo (3 nodes ×
# {beat, worker}) without losing isolation because the worker only
# processes the crdt_sync queue for gossip. Any failure in either
# subprocess exits the container so docker can restart the pair
# together.
set -eu

echo "[federation/worker] waiting for database to accept migrations ..."
# Give the backend a moment to finish Alembic before kicking off the
# worker; otherwise the worker might start before the recordstate enum
# exists and fail on first SQL statement.
sleep 5

echo "[federation/worker] starting celery beat ..."
celery -A app.workers.celery_app beat --loglevel=info &
BEAT_PID=$!

echo "[federation/worker] starting celery worker on crdt_sync queue ..."
# Single worker process covers the crdt_sync queue that the gossip task
# is routed to in celery_app.py. Health/trust/memory/workflow queues
# keep going via the main docker-compose worker; in the federation demo
# we only care about sync.
celery -A app.workers.celery_app worker \
    --loglevel=info \
    --queues=crdt_sync,health,trust,memory,workflow,audit \
    --concurrency=1 &
WORKER_PID=$!

# Exit the container if either subprocess dies so docker restart policy
# brings them back together (not as an orphaned beat with no worker).
wait -n "$BEAT_PID" "$WORKER_PID"
EXIT_CODE=$?
kill "$BEAT_PID" "$WORKER_PID" 2>/dev/null || true
exit $EXIT_CODE
