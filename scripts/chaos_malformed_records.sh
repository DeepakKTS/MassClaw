#!/usr/bin/env bash
# chaos_malformed_records.sh — feed every class of bad input into the
# API surface that stock agents hit. Every one should return a
# structured JSON envelope with error_code + detail — never a 500
# stack trace and never a plain text body.
#
# Usage: $0 [node-a|node-b|node-c]  (default: node-a)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

NODE="${1:-node-a}"
fed::assert_valid_node "$NODE"
URL="$(fed::node_url "$NODE")"

fed::info "=== chaos_malformed_records against $URL ==="

fail=0
check() {
  local name="$1"; shift
  local expected_code="$1"; shift
  local status body
  status=$(curl -s -o /tmp/chaos_body -w '%{http_code}' "$@")
  body=$(cat /tmp/chaos_body)
  if [ "$status" != "$expected_code" ]; then
    fed::info "FAIL $name — expected $expected_code, got $status; body=$body"
    fail=$((fail+1))
    return
  fi
  # Every error response must parse as JSON and carry error_code + detail.
  if ! echo "$body" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "error_code" in d, f"missing error_code: {d!r}"
assert "detail" in d, f"missing detail: {d!r}"
' >/dev/null 2>&1; then
    fed::info "FAIL $name — body is not a well-formed envelope: $body"
    fail=$((fail+1))
    return
  fi
  fed::ok "$name → $status with envelope"
}

check "malformed JSON body" 422 -X POST -H "Content-Type: application/json" \
  --data-raw '{not json' "$URL/api/v1/policy/registry/evaluate"

check "wrong types in body" 422 -X POST -H "Content-Type: application/json" \
  --data-raw '{"action":"x","estimated_cost":"not-a-number"}' \
  "$URL/api/v1/policy/registry/evaluate"

check "invalid uuid path param" 422 \
  "$URL/api/v1/workflows/not-a-uuid/status"

check "unknown route" 404 "$URL/api/v1/totally/fake/path"

check "wrong method on /policy/registry/rules" 405 -X DELETE \
  "$URL/api/v1/policy/registry/rules"

check "oversized body" 413 -X POST -H "Content-Type: application/json" \
  --data-binary "@$(python3 -c 'import os,tempfile; f=tempfile.NamedTemporaryFile("wb", delete=False); f.write(b"x"*(2*1024*1024)); f.close(); print(f.name)')" \
  "$URL/api/v1/policy/registry/evaluate" || true

if [ "$fail" -gt 0 ]; then
  fed::die "$fail chaos assertion(s) failed"
fi

fed::ok "every malformed-input probe returned a proper envelope"
