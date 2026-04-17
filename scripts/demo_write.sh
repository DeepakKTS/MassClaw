#!/usr/bin/env bash
# demo_write.sh — write a self-signed memory record to one node.
#
# Usage: scripts/demo_write.sh <node> '<content>' [--confidence 0.9]
# Example:
#   scripts/demo_write.sh node-a 'deadline: April 30'
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

if [ $# -lt 2 ]; then
  fed::die "usage: $0 <node> '<content>' [--confidence 0.9]"
fi

NODE="$1"
CONTENT="$2"
shift 2
CONFIDENCE="0.9"
while [ $# -gt 0 ]; do
  case "$1" in
    --confidence) CONFIDENCE="$2"; shift 2 ;;
    *) fed::die "unknown flag: $1" ;;
  esac
done

fed::assert_valid_node "$NODE"

BODY=$(cat <<JSON
{
  "workflow_id": "$FED_DEMO_WORKFLOW_ID",
  "content": $(fed::json_string "$CONTENT"),
  "confidence": $CONFIDENCE,
  "metadata": {"source": "demo_write.sh", "node": "$NODE"},
  "memory_type": "result"
}
JSON
)

RESPONSE=$(fed::http_post "$NODE" "/api/v1/demo/self-sign-write" "$BODY")
if [ -z "$RESPONSE" ]; then
  fed::die "no response body from $NODE — check 'docker compose logs backend-${NODE#node-}'"
fi

HASH=$(printf '%s' "$RESPONSE" | fed::jq_or '.hash // .content_hash // empty')
DID=$(printf '%s' "$RESPONSE" | fed::jq_or '.author_did // empty')

if [ -z "$HASH" ]; then
  fed::die "server did not return a hash — response: $RESPONSE"
fi

fed::ok "$NODE signed + stored record"
fed::info "  author did : ${DID:-unknown}"
fed::info "  hash       : $HASH"
fed::info "  content    : $CONTENT"
printf '%s\n' "$HASH"
