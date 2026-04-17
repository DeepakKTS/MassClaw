#!/usr/bin/env bash
# demo_up.sh — bring up the 3-node federation and seed a shared workflow.
#
# Usage: scripts/demo_up.sh [--wait-seconds N]
# Exit codes: 0 on success; non-zero if any node fails to become healthy.
set -euo pipefail

# Load shared helpers (colours, node → port map, API calls).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

WAIT_SECONDS=90
while [ $# -gt 0 ]; do
  case "$1" in
    --wait-seconds) WAIT_SECONDS="$2"; shift 2 ;;
    *) fed::die "unknown flag: $1" ;;
  esac
done

fed::require_docker

fed::info "building images + starting the 12-container federation"
(
  cd "$FED_REPO_ROOT"
  docker compose -f "$FED_COMPOSE_FILE" up -d --build
)

fed::info "waiting up to ${WAIT_SECONDS}s for every node to report /health OK"
fed::wait_for_health node-a "$WAIT_SECONDS"
fed::wait_for_health node-b "$WAIT_SECONDS"
fed::wait_for_health node-c "$WAIT_SECONDS"

fed::info "seeding the shared demo workflow ($FED_DEMO_WORKFLOW_ID) on all three nodes"
for node in node-a node-b node-c; do
  fed::seed_workflow "$node"
done

fed::ok "federation is up."
fed::info "node URLs:"
fed::info "  node-a → http://localhost:${FED_PORT_A}"
fed::info "  node-b → http://localhost:${FED_PORT_B}"
fed::info "  node-c → http://localhost:${FED_PORT_C}"
fed::info "next steps:"
fed::info "  ./scripts/demo_write.sh node-a 'deadline: April 30'"
fed::info "  ./scripts/demo_partition.sh node-c"
fed::info "  ./scripts/demo_query.sh node-a 'what is the deadline'"
fed::info "  ./scripts/demo_federation_test.sh   # full end-to-end scenario"
