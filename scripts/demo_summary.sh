#!/usr/bin/env bash
# demo_summary.sh — print each node's Merkle root so you can eyeball convergence.
#
# Usage: scripts/demo_summary.sh [--watch N]   # refresh every N seconds
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

WATCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --watch) WATCH="$2"; shift 2 ;;
    *) fed::die "unknown flag: $1" ;;
  esac
done

print_table() {
  printf '\n%-10s %-20s %-15s\n' "NODE" "MERKLE ROOT" "RECORDS"
  printf '%-10s %-20s %-15s\n' "------" "---------------" "-------"
  for node in node-a node-b node-c; do
    ROOT=$(fed::merkle_root "$node" || echo "unreachable")
    COUNT=$(fed::merkle_record_count "$node" || echo "?")
    SHORT_ROOT=$(printf '%s' "$ROOT" | cut -c1-16)
    printf '%-10s %-20s %-15s\n' "$node" "${SHORT_ROOT}…" "$COUNT"
  done
  printf '\n'
}

if [ -n "$WATCH" ]; then
  while true; do
    clear
    date -u
    print_table
    sleep "$WATCH"
  done
else
  print_table
fi
