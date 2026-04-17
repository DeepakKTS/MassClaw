#!/usr/bin/env bash
# demo_partition.sh — isolate one node from the federation network.
#
# Uses `docker network disconnect`, not iptables, so the effect is a real
# drop of L3 connectivity between the node and its peers. Re-attach with
# scripts/demo_heal.sh.
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

fed::info "disconnecting $BACKEND_CONTAINER + $WORKER_CONTAINER from $FED_NETWORK"
docker network disconnect "$FED_NETWORK" "$BACKEND_CONTAINER" 2>/dev/null || \
  fed::info "  ($BACKEND_CONTAINER already disconnected)"
docker network disconnect "$FED_NETWORK" "$WORKER_CONTAINER" 2>/dev/null || \
  fed::info "  ($WORKER_CONTAINER already disconnected)"

fed::ok "$NODE is partitioned. Writes to this node will not reach peers until demo_heal.sh runs."
