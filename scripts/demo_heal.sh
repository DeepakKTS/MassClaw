#!/usr/bin/env bash
# demo_heal.sh — reattach a partitioned node and let gossip converge.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

if [ $# -lt 1 ]; then
  fed::die "usage: $0 <node>"
fi

NODE="$1"
fed::assert_valid_node "$NODE"
BACKEND_CONTAINER=$(fed::backend_container "$NODE")
WORKER_CONTAINER=$(fed::worker_container "$NODE")

fed::info "reconnecting $BACKEND_CONTAINER + $WORKER_CONTAINER to $FED_NETWORK"
docker network connect "$FED_NETWORK" "$BACKEND_CONTAINER" 2>/dev/null || \
  fed::info "  ($BACKEND_CONTAINER already attached)"
docker network connect "$FED_NETWORK" "$WORKER_CONTAINER" 2>/dev/null || \
  fed::info "  ($WORKER_CONTAINER already attached)"

fed::info "waiting up to 30s for $NODE to report /health OK again"
fed::wait_for_health "$NODE" 30
fed::ok "$NODE reattached. Gossip should converge within the next few ticks."
