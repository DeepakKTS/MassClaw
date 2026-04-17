#!/usr/bin/env bash
# dev_federation_up.sh — bring up a 3-node MassClaw federation as native
# processes (no Docker). Uses Homebrew Postgres + Redis directly.
#
# Creates or reuses databases:
#   massclaw_fed_a / massclaw_fed_b / massclaw_fed_c  (all owned by user `massclaw`)
# Uses Redis DB indices:
#   data: 10/11/12   celery: 13/14/15
# Host ports:
#   backend: 18001 / 18002 / 18003
#
# Logs to /tmp/massclaw_fed/node-{a,b,c}.log + celery-{a,b,c}.log.
# PIDs to /tmp/massclaw_fed/*.pid.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME="/tmp/massclaw_fed"
CONDA_ENV="${MASSCLAW_CONDA_ENV:-massclaw-fed}"
CONDA_SH="/Users/deepakzedler/miniconda3/etc/profile.d/conda.sh"

mkdir -p "$RUNTIME"

# shellcheck disable=SC1091
source "$CONDA_SH"
conda activate "$CONDA_ENV"

log_info()  { printf "\033[1;34m[fed-up]\033[0m %s\n" "$*"; }
log_ok()    { printf "\033[1;32m[ok]\033[0m %s\n" "$*"; }
log_warn()  { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
log_err()   { printf "\033[1;31m[err]\033[0m %s\n" "$*"; }

# ----------------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------------
command -v psql >/dev/null || { log_err "psql not found (brew install postgresql@17)"; exit 1; }
command -v redis-cli >/dev/null || { log_err "redis-cli not found (brew install redis)"; exit 1; }
command -v uvicorn >/dev/null || { log_err "uvicorn not in conda env '$CONDA_ENV'"; exit 1; }
command -v celery >/dev/null || { log_err "celery not in conda env '$CONDA_ENV'"; exit 1; }

PGPASSWORD=massclaw psql -U massclaw -h localhost -d massclaw -c "SELECT 1" >/dev/null 2>&1 || {
  log_err "cannot connect to postgres as massclaw/massclaw — is Homebrew postgresql@17 running?"
  exit 1
}
redis-cli ping >/dev/null || { log_err "redis-cli ping failed"; exit 1; }

# ----------------------------------------------------------------------------
# Create databases + extensions (idempotent)
# ----------------------------------------------------------------------------
# Determine an admin role that can CREATE DATABASE and CREATE EXTENSION.
# pgvector requires a superuser to install. On Homebrew postgres, the OS user
# (e.g., `deepakzedler`) is usually a superuser; the massclaw role is not.
ADMIN_ROLE="${MASSCLAW_PG_ADMIN:-$USER}"
if ! psql -U "$ADMIN_ROLE" -d postgres -tAc "SELECT 1" >/dev/null 2>&1; then
  log_err "cannot connect to postgres as admin role '$ADMIN_ROLE' — set MASSCLAW_PG_ADMIN or ensure your OS user is a pg superuser"
  exit 1
fi

for suffix in a b c; do
  db="massclaw_fed_$suffix"
  exists=$(psql -U "$ADMIN_ROLE" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" 2>/dev/null || echo "")
  if [ "$exists" != "1" ]; then
    log_info "creating database $db (as $ADMIN_ROLE, owner=massclaw)"
    psql -U "$ADMIN_ROLE" -d postgres -c "CREATE DATABASE $db OWNER massclaw" >/dev/null
  fi
  # Extensions (require superuser for pgvector)
  psql -U "$ADMIN_ROLE" -d "$db" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
  psql -U "$ADMIN_ROLE" -d "$db" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" >/dev/null
  psql -U "$ADMIN_ROLE" -d "$db" -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"' >/dev/null
  # Ensure massclaw can use all objects in public schema
  psql -U "$ADMIN_ROLE" -d "$db" -c "GRANT ALL ON SCHEMA public TO massclaw" >/dev/null
  psql -U "$ADMIN_ROLE" -d "$db" -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO massclaw" >/dev/null
done
log_ok "databases ready: massclaw_fed_a / _b / _c"

# ----------------------------------------------------------------------------
# Compute per-node env
# ----------------------------------------------------------------------------
node_env() {
  local suffix="$1"   # a|b|c
  local port="$2"     # 18001|18002|18003
  local peer_ports="$3"
  local db="massclaw_fed_$suffix"
  local peers=""
  for p in $peer_ports; do
    peers="${peers}http://localhost:${p},"
  done
  peers="${peers%,}"

  local data_idx celery_idx
  case "$suffix" in
    a) data_idx=10; celery_idx=13 ;;
    b) data_idx=11; celery_idx=14 ;;
    c) data_idx=12; celery_idx=15 ;;
  esac

  # Pull ANTHROPIC_API_KEY / OPENAI_API_KEY / BRAVE_SEARCH_API_KEY / etc. from
  # backend/.env if the launching shell doesn't already have them. This keeps
  # the federation's LLM planners working without forcing the operator to
  # manually export the keys before `make fed-native-up`.
  local env_file="$REPO_ROOT/backend/.env"
  local anthropic_key="${ANTHROPIC_API_KEY:-}"
  local openai_key="${OPENAI_API_KEY:-}"
  local brave_key="${BRAVE_SEARCH_API_KEY:-}"
  local firecrawl_key="${FIRECRAWL_API_KEY:-}"
  if [ -z "$anthropic_key" ] && [ -f "$env_file" ]; then
    anthropic_key=$(grep -E '^ANTHROPIC_API_KEY=' "$env_file" | head -1 | cut -d= -f2-)
  fi
  if [ -z "$openai_key" ] && [ -f "$env_file" ]; then
    openai_key=$(grep -E '^OPENAI_API_KEY=' "$env_file" | head -1 | cut -d= -f2-)
  fi
  if [ -z "$brave_key" ] && [ -f "$env_file" ]; then
    brave_key=$(grep -E '^BRAVE_SEARCH_API_KEY=' "$env_file" | head -1 | cut -d= -f2-)
  fi
  if [ -z "$firecrawl_key" ] && [ -f "$env_file" ]; then
    firecrawl_key=$(grep -E '^FIRECRAWL_API_KEY=' "$env_file" | head -1 | cut -d= -f2-)
  fi

  cat <<EOF
export DATABASE_URL="postgresql+asyncpg://massclaw:massclaw@localhost:5432/${db}"
export DATABASE_SYNC_URL="postgresql://massclaw:massclaw@localhost:5432/${db}"
export REDIS_URL="redis://localhost:6379/${data_idx}"
export CELERY_BROKER_URL="redis://localhost:6379/${celery_idx}"
export CELERY_RESULT_BACKEND="redis://localhost:6379/${celery_idx}"
export AUTH_REQUIRED="false"
export LOG_LEVEL="INFO"
export LOG_FORMAT="console"
export ANTHROPIC_API_KEY="${anthropic_key}"
export OPENAI_API_KEY="${openai_key}"
export BRAVE_SEARCH_API_KEY="${brave_key}"
export FIRECRAWL_API_KEY="${firecrawl_key}"
export EMBEDDING_MODEL="all-MiniLM-L6-v2"
export JWT_SECRET_KEY="federation-demo-not-for-production"
export IDENTITY_KEY_ENCRYPTION_KEY="00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
export IDENTITY_INSTANCE_KEY_PATH="${RUNTIME}/node-${suffix}.key"
export IDENTITY_PUBLIC_BASE_URL="http://localhost:${port}"
export IDENTITY_TRUST_ZONE="federation-local"
export IDENTITY_INSTANCE_NAME="MassClaw (node-${suffix})"
export IDENTITY_INSTANCE_DESCRIPTION="Local federation node ${suffix}"
export MASSCLAW_DEMO_MODE="true"
export MASSCLAW_FEDERATION_PEERS="${peers}"
export GOSSIP_INTERVAL_SECONDS="5"
export GOSSIP_FANOUT="2"
export NANDA_INDEX_ENABLED="false"
export NANDA_INDEX_REGISTER_ON_STARTUP="false"
export HEALTH_CHECK_INTERVAL_SECONDS="60"
export CORS_ORIGINS='["http://localhost:3000","http://localhost:18001","http://localhost:18002","http://localhost:18003","http://localhost:19000"]'
EOF
}

# ----------------------------------------------------------------------------
# Alembic: run migrations on each DB
# ----------------------------------------------------------------------------
log_info "running alembic migrations on all 3 databases"
for entry in "a:18001:18002 18003" "b:18002:18001 18003" "c:18003:18001 18002"; do
  IFS=':' read -r suffix port peers <<< "$entry"
  (
    cd "$REPO_ROOT/backend"
    eval "$(node_env "$suffix" "$port" "$peers")"
    alembic upgrade head >> "$RUNTIME/alembic-${suffix}.log" 2>&1
  ) || { log_err "alembic failed for node-${suffix} (see $RUNTIME/alembic-${suffix}.log)"; exit 1; }
done
log_ok "migrations applied"

# ----------------------------------------------------------------------------
# Start uvicorn + celery per node
# ----------------------------------------------------------------------------
start_node() {
  local suffix="$1" port="$2" peers="$3"
  local name="node-${suffix}"
  local pid_file="${RUNTIME}/${name}.pid"

  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    log_warn "$name already running (pid $(cat "$pid_file"))"
    return 0
  fi

  # Uvicorn
  (
    cd "$REPO_ROOT/backend"
    eval "$(node_env "$suffix" "$port" "$peers")"
    nohup uvicorn app.main:app --host 0.0.0.0 --port "$port" --workers 1 \
      > "${RUNTIME}/${name}.log" 2>&1 &
    echo $! > "$pid_file"
  )

  # Celery (worker + beat combined).
  #
  # IMPORTANT: cap the file-descriptor soft limit before launching. On
  # modern macOS the default `ulimit -n` is so high that billiard's
  # close_open_fds() — used by celery beat at startup — overflows
  # Python's int-to-C-int conversion and crashes the beat process with
  # "OverflowError: Python int too large to convert to C int". When that
  # happens the worker starts fine but no scheduled tasks ever fire,
  # which silently breaks the entire gossip protocol (records write to
  # the local node but never propagate to peers). Capping at 1024 keeps
  # the iteration range well within a C int and costs nothing for our
  # workload (we never open thousands of fds).
  local celery_pid_file="${RUNTIME}/celery-${suffix}.pid"
  (
    cd "$REPO_ROOT/backend"
    eval "$(node_env "$suffix" "$port" "$peers")"
    ulimit -n 1024
    nohup celery -A app.workers.celery_app worker --beat \
      -Q crdt_sync,health,trust,memory,workflow,audit -l info \
      > "${RUNTIME}/celery-${suffix}.log" 2>&1 &
    echo $! > "$celery_pid_file"
  )

  log_info "started ${name}: backend pid=$(cat "$pid_file") celery pid=$(cat "$celery_pid_file") port=${port}"
}

start_node a 18001 "18002 18003"
start_node b 18002 "18001 18003"
start_node c 18003 "18001 18002"

# ----------------------------------------------------------------------------
# Wait for health
# ----------------------------------------------------------------------------
wait_health() {
  local port="$1" timeout="${2:-60}"
  for _ in $(seq 1 "$timeout"); do
    if curl -sf "http://localhost:${port}/health" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

log_info "waiting up to 60s for each node's /health"
for port in 18001 18002 18003; do
  if wait_health "$port" 60; then
    log_ok "node on :$port is healthy"
  else
    log_err "node on :$port did not become healthy — tail $RUNTIME/node-*.log"
    exit 1
  fi
done

# Seed the shared demo workflow so write scripts can use it immediately
FED_DEMO_WORKFLOW_ID="00000000-0000-4000-8000-000000000001"
for port in 18001 18002 18003; do
  curl -sf -X POST "http://localhost:${port}/api/v1/demo/seed-workflow" \
    -H 'Content-Type: application/json' \
    -d "{\"workflow_id\":\"$FED_DEMO_WORKFLOW_ID\"}" >/dev/null \
    && log_ok "seeded demo workflow on :$port" \
    || log_warn "could not seed demo workflow on :$port (continuing)"
done

log_ok "federation is up:"
log_info "  node-a → http://localhost:18001"
log_info "  node-b → http://localhost:18002"
log_info "  node-c → http://localhost:18003"
log_info "logs → $RUNTIME/node-{a,b,c}.log, celery-{a,b,c}.log"
log_info "tear down: scripts/dev_federation_down.sh"
