#!/usr/bin/env bash
# chaos_partition_mid_write.sh — kill gossip network while a write is still
# propagating. Verifies the CRDT convergence primitive against the nastiest
# realistic failure mode (lossy partition mid-replication).
#
# What it does:
#   1. Bring up the 3-node federation.
#   2. Write R1 on node-a. Immediately partition node-c.
#   3. Write R2 on node-b. Partition node-b. Write R3 on node-a.
#   4. Heal both partitions.
#   5. Poll until all three Merkle roots converge, or fail after 60s.
#
# Exit 0 = converged. Non-zero = divergence survived the heal, which is
# a real bug the CI should catch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

TIMEOUT_S=${CHAOS_TIMEOUT_S:-60}

fed::info "=== chaos_partition_mid_write ==="
"$SCRIPT_DIR/demo_up.sh"

fed::info "writing R1 on node-a"
"$SCRIPT_DIR/demo_write.sh" node-a "fact-R1: deadline Apr 30" --confidence 0.93 >/dev/null

fed::info "partitioning node-c"
"$SCRIPT_DIR/demo_partition.sh" node-c >/dev/null

fed::info "writing R2 on node-b (reaches a, not c)"
"$SCRIPT_DIR/demo_write.sh" node-b "fact-R2: deadline May 15" --confidence 0.88 >/dev/null

fed::info "partitioning node-b"
"$SCRIPT_DIR/demo_partition.sh" node-b >/dev/null

fed::info "writing R3 on node-a (only a has it now)"
"$SCRIPT_DIR/demo_write.sh" node-a "fact-R3: deadline Apr 28" --confidence 0.71 >/dev/null

fed::info "healing node-b and node-c"
"$SCRIPT_DIR/demo_heal.sh" node-b >/dev/null
"$SCRIPT_DIR/demo_heal.sh" node-c >/dev/null

fed::info "waiting up to ${TIMEOUT_S}s for convergence"
START=$(date +%s)
while true; do
  if "$SCRIPT_DIR/demo_summary.sh" 2>/dev/null | awk '/merkle_root/ {print $NF}' | sort -u | \
     awk 'END { if (NR == 1) exit 0; else exit 1 }'; then
    fed::ok "federation converged after $(( $(date +%s) - START ))s"
    exit 0
  fi
  if (( $(date +%s) - START >= TIMEOUT_S )); then
    fed::die "federation did not converge within ${TIMEOUT_S}s — see demo_summary.sh for divergence"
  fi
  sleep 2
done
