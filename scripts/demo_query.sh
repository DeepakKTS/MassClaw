#!/usr/bin/env bash
# demo_query.sh — resolve a fact across the CRDT set on one node.
#
# Usage: scripts/demo_query.sh <node> '<subject>' [--mode planning|audit|sensitive]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

if [ $# -lt 2 ]; then
  fed::die "usage: $0 <node> '<subject>' [--mode planning|audit|sensitive]"
fi

NODE="$1"
SUBJECT="$2"
shift 2
MODE="audit"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    *) fed::die "unknown flag: $1" ;;
  esac
done

fed::assert_valid_node "$NODE"

BODY=$(cat <<JSON
{
  "subject": $(fed::json_string "$SUBJECT"),
  "mode": "$MODE",
  "workflow_id": "$FED_DEMO_WORKFLOW_ID",
  "min_similarity": 0.0,
  "top_k": 20
}
JSON
)

RESPONSE=$(fed::http_post "$NODE" "/api/v1/memory/facts/resolve" "$BODY")
fed::info "$NODE · mode=$MODE · subject='$SUBJECT'"
printf '%s\n' "$RESPONSE" | fed::pretty_json
