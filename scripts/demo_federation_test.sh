#!/usr/bin/env bash
# demo_federation_test.sh — end-to-end partition/heal/converge scenario.
#
# This is the "three deadlines" demo from the design doc, executed live.
# Exit code is 0 only if every assertion passes.
#
# Steps:
#   1. Bring the federation up (if not already).
#   2. Write "deadline: April 30" on node-a.
#   3. Write "deadline: May 15"  on node-b.
#   4. Wait for gossip → all three nodes should agree on the two records.
#   5. Partition node-c from the federation network.
#   6. Write "deadline: April 28" on node-c (offline write).
#   7. Write "budget: $500" on node-b (reaches node-a via gossip, not node-c).
#   8. Heal node-c.
#   9. Wait ≤10 seconds for convergence.
#   10. Assert every node has all four signed records.
#
# Usage: scripts/demo_federation_test.sh [--skip-up]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

SKIP_UP="${1:-}"
if [ "$SKIP_UP" = "--skip-up" ]; then
  fed::info "skipping federation bring-up (assuming it's already running)"
else
  "$SCRIPT_DIR/demo_up.sh"
fi

fed::info "======================================================================"
fed::info "STEP 1/4 — writing two records (node-a, node-b) and letting gossip run"
fed::info "======================================================================"
HASH_APR30=$("$SCRIPT_DIR/demo_write.sh" node-a "deadline: April 30" --confidence 0.9 | tail -n1)
HASH_MAY15=$("$SCRIPT_DIR/demo_write.sh" node-b "deadline: May 15"   --confidence 0.88 | tail -n1)

fed::info "sleeping 12s so gossip propagates the baseline (interval=5s, fanout=2)"
sleep 12
"$SCRIPT_DIR/demo_summary.sh"

fed::info "======================================================================"
fed::info "STEP 2/4 — partitioning node-c and writing divergent records"
fed::info "======================================================================"
"$SCRIPT_DIR/demo_partition.sh" node-c

# node-c is partitioned; its demo_write still succeeds locally because
# the backend isn't on the network — it IS still listening on its host
# port. The record it writes is isolated until we heal.
HASH_APR28=$("$SCRIPT_DIR/demo_write.sh" node-c "deadline: April 28" --confidence 0.71 | tail -n1)
HASH_BUDGET=$("$SCRIPT_DIR/demo_write.sh" node-b "budget: \$500" --confidence 0.85 | tail -n1)

fed::info "sleeping 8s so node-a + node-b gossip while node-c is isolated"
sleep 8
"$SCRIPT_DIR/demo_summary.sh"

fed::info "======================================================================"
fed::info "STEP 3/4 — healing the partition"
fed::info "======================================================================"
"$SCRIPT_DIR/demo_heal.sh" node-c

fed::info "waiting up to 15s for the three Merkle roots to match"
CONVERGED=0
for i in $(seq 1 15); do
  ROOT_A=$(fed::merkle_root node-a || echo "")
  ROOT_B=$(fed::merkle_root node-b || echo "")
  ROOT_C=$(fed::merkle_root node-c || echo "")
  if [ -n "$ROOT_A" ] && [ "$ROOT_A" = "$ROOT_B" ] && [ "$ROOT_B" = "$ROOT_C" ]; then
    CONVERGED=1
    fed::ok "converged after ${i}s (root=${ROOT_A:0:16}…)"
    break
  fi
  sleep 1
done

"$SCRIPT_DIR/demo_summary.sh"

fed::info "======================================================================"
fed::info "STEP 4/4 — assertions"
fed::info "======================================================================"

FAIL=0

assert_hash_on_node() {
  local node="$1"; local hash="$2"; local label="$3"
  if fed::http_get "$node" "/api/v1/memory/by-hash/$hash" | grep -q '"author_did"'; then
    fed::ok "$node has '$label' ($hash)"
  else
    fed::err "$node MISSING '$label' ($hash)"
    FAIL=1
  fi
}

for node in node-a node-b node-c; do
  assert_hash_on_node "$node" "$HASH_APR30"  "deadline: April 30"
  assert_hash_on_node "$node" "$HASH_MAY15"  "deadline: May 15"
  assert_hash_on_node "$node" "$HASH_APR28"  "deadline: April 28 (was partitioned)"
  assert_hash_on_node "$node" "$HASH_BUDGET" "budget: \$500"
done

if [ "$CONVERGED" -ne 1 ]; then
  fed::err "Merkle roots did not converge within the 15s budget"
  FAIL=1
fi

fed::info ""
fed::info "querying 'what is the deadline' in audit mode on node-a:"
"$SCRIPT_DIR/demo_query.sh" node-a "what is the deadline" --mode audit

if [ "$FAIL" -eq 0 ]; then
  fed::ok "FEDERATION CONVERGENCE TEST PASSED."
  fed::info "all four records are visible on all three nodes, Merkle roots match."
  exit 0
else
  fed::err "FEDERATION CONVERGENCE TEST FAILED. See assertions above."
  exit 1
fi
