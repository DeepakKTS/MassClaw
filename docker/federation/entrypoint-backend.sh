#!/bin/sh
# Backend entrypoint for a federation node.
#
# Every node starts from an empty database (tmpfs volume), so we must run
# migrations on boot. A single Alembic invocation is idempotent — already-
# applied migrations are no-ops — so restarts stay cheap.
set -eu

echo "[federation/backend] running alembic upgrade head ..."
alembic upgrade head

echo "[federation/backend] starting uvicorn on 0.0.0.0:8000 ..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --proxy-headers
