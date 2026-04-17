#!/usr/bin/env bash
# dev_federation_down.sh — stop the native 3-node federation started by
# dev_federation_up.sh. Reads PIDs from /tmp/massclaw_fed and SIGTERMs them.
# --purge also drops the databases and deletes runtime files.

set -euo pipefail

RUNTIME="/tmp/massclaw_fed"
PURGE="${1:-}"

log_info()  { printf "\033[1;34m[fed-down]\033[0m %s\n" "$*"; }
log_ok()    { printf "\033[1;32m[ok]\033[0m %s\n" "$*"; }
log_warn()  { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }

stop_pid() {
  local pid_file="$1" name="$2"
  if [ -f "$pid_file" ]; then
    local pid
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      # Grace period for uvicorn + celery to flush
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      log_info "stopped $name (pid $pid)"
    fi
    rm -f "$pid_file"
  fi
}

for suffix in a b c; do
  stop_pid "$RUNTIME/node-${suffix}.pid" "node-${suffix}"
  stop_pid "$RUNTIME/celery-${suffix}.pid" "celery-${suffix}"
done

# Belt-and-suspenders: kill any orphaned uvicorn/celery listening on our ports
for port in 18001 18002 18003; do
  lsof -nP -i "TCP:${port}" -sTCP:LISTEN -t 2>/dev/null | while read -r pid; do
    log_warn "killing leftover process on :$port (pid $pid)"
    kill "$pid" 2>/dev/null || true
  done
done

log_ok "federation stopped"

if [ "$PURGE" = "--purge" ]; then
  log_info "purging runtime + databases"
  for suffix in a b c; do
    PGPASSWORD=massclaw psql -U massclaw -h localhost -d postgres -c "DROP DATABASE IF EXISTS massclaw_fed_${suffix}" >/dev/null 2>&1 || true
  done
  rm -rf "$RUNTIME"
  log_ok "purged"
fi
