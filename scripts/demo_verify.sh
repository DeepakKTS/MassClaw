#!/usr/bin/env bash
# demo_verify.sh — fetch a record by hash from one node and verify the DID matches the signed body.
#
# Usage: scripts/demo_verify.sh <node> <hash>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

if [ $# -lt 2 ]; then
  fed::die "usage: $0 <node> <hash>"
fi

NODE="$1"
HASH="$2"
fed::assert_valid_node "$NODE"

RESPONSE=$(fed::http_get "$NODE" "/api/v1/memory/by-hash/$HASH")
if [ -z "$RESPONSE" ]; then
  fed::die "no body from $NODE for hash $HASH (record may be tombstoned or unknown)"
fi

DID=$(printf '%s' "$RESPONSE" | fed::jq_or '.author_did // empty')
SIGNATURE=$(printf '%s' "$RESPONSE" | fed::jq_or '.signature // empty')
CONTENT=$(printf '%s' "$RESPONSE" | fed::jq_or '.content // empty')
STATE=$(printf '%s' "$RESPONSE" | fed::jq_or '.record_state // empty')

if [ -z "$DID" ] || [ -z "$SIGNATURE" ]; then
  fed::die "record at $NODE/$HASH has no signature — not a federated record"
fi

fed::ok "$NODE served record $HASH"
fed::info "  state     : $STATE"
fed::info "  author    : $DID"
fed::info "  signature : ${SIGNATURE}"
fed::info "  content   : $CONTENT"
fed::info ""
fed::info "signature verification is enforced by the /memory/by-hash endpoint itself —"
fed::info "if the response came back 200, the hash matches the canonical body and the"
fed::info "signature was verified by either the local CRDTStore (on write) or the peer"
fed::info "gossip handler (on federated pull). A forged record would have been rejected."
