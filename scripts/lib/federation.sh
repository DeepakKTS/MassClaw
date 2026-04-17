#!/usr/bin/env bash
# Shared helpers for the federation demo scripts.
#
# Source this file from any demo_*.sh script. It deliberately avoids fancy
# bash features so it runs on macOS default bash 3.2 as well as Linux
# bash 5.x — no associative arrays, no process substitution.

set -euo pipefail

# Resolve the repository root from this helper's own path so the scripts
# work regardless of CWD.
FED_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FED_REPO_ROOT="$(cd "$FED_LIB_DIR/../.." && pwd)"
FED_COMPOSE_FILE="docker/federation.yml"
FED_NETWORK="massclaw-fed-network"
FED_PORT_A=18001
FED_PORT_B=18002
FED_PORT_C=18003

# A single, well-known workflow id so every node's records FK-resolve.
FED_DEMO_WORKFLOW_ID="${FED_DEMO_WORKFLOW_ID:-00000000-0000-4000-8000-000000000001}"

# Minimal colour support when stdout is a TTY.
if [ -t 1 ]; then
  FED_C_DIM='\033[2m'
  FED_C_RESET='\033[0m'
  FED_C_OK='\033[32m'
  FED_C_WARN='\033[33m'
  FED_C_ERR='\033[31m'
else
  FED_C_DIM=''; FED_C_RESET=''; FED_C_OK=''; FED_C_WARN=''; FED_C_ERR=''
fi

fed::info() { printf "${FED_C_DIM}[fed] %s${FED_C_RESET}\n" "$*" >&2; }
fed::ok()   { printf "${FED_C_OK}[ok] %s${FED_C_RESET}\n" "$*" >&2; }
fed::warn() { printf "${FED_C_WARN}[warn] %s${FED_C_RESET}\n" "$*" >&2; }
fed::err()  { printf "${FED_C_ERR}[error] %s${FED_C_RESET}\n" "$*" >&2; }
fed::die()  { fed::err "$*"; exit 2; }

fed::require_docker() {
  command -v docker >/dev/null 2>&1 || fed::die "'docker' not found on PATH. Install Docker Desktop or Colima."
  docker info >/dev/null 2>&1 || fed::die "docker daemon not reachable — start Docker Desktop / colima first."
  if ! docker compose version >/dev/null 2>&1; then
    fed::die "'docker compose' subcommand not available. Upgrade Docker to v20.10.13+."
  fi
}

fed::assert_valid_node() {
  case "${1:-}" in
    node-a|node-b|node-c) return 0 ;;
    *) fed::die "node must be one of: node-a, node-b, node-c (got '${1:-}')" ;;
  esac
}

fed::port_for() {
  case "$1" in
    node-a) printf '%s' "$FED_PORT_A" ;;
    node-b) printf '%s' "$FED_PORT_B" ;;
    node-c) printf '%s' "$FED_PORT_C" ;;
    *) return 1 ;;
  esac
}

fed::backend_container() {
  printf 'massclaw-fed-backend-%s' "${1#node-}"
}

fed::worker_container() {
  printf 'massclaw-fed-worker-%s' "${1#node-}"
}

fed::http_get() {
  local node="$1"; local path="$2"
  local port; port=$(fed::port_for "$node") || fed::die "unknown node '$node'"
  curl -sS --max-time 15 -H 'Accept: application/json' "http://localhost:${port}${path}"
}

fed::http_post() {
  local node="$1"; local path="$2"; local body="$3"
  local port; port=$(fed::port_for "$node") || fed::die "unknown node '$node'"
  curl -sS --max-time 15 \
    -H 'Accept: application/json' -H 'Content-Type: application/json' \
    -d "$body" \
    "http://localhost:${port}${path}"
}

# Send the three peer-auth headers so a caller can hit the /memory/sync
# endpoints directly from a script for debugging. Uses a throwaway
# keypair re-generated per invocation; only useful when the target has an
# empty allowlist (local-dev mode, which is what federation.yml uses).
fed::sync_summary() {
  local node="$1"
  local port; port=$(fed::port_for "$node") || fed::die "unknown node '$node'"
  # Ask the node itself via /memory/sync/summary with a minimal shell-
  # signed header set is too fiddly in bash; rely on the app's public
  # surface instead. The sync endpoint requires peer auth, so we sign
  # via a small inline Python helper when python is available.
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import sys, time, json, urllib.request, hashlib, os, subprocess, shutil
port = sys.argv[1]
# Lazily import the app's signer — path added from the repo checkout.
repo = os.environ.get("FED_REPO_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(repo, "backend"))
try:
    from app.identity.signer import generate_keypair, encode_multibase, sign_bytes
    from app.identity.did import build_did_key
except Exception as exc:
    print(json.dumps({"error": f"cannot import app signer: {exc}"}))
    sys.exit(0)
kp = generate_keypair()
did = build_did_key(kp.public_bytes)
ts = str(int(time.time()))
path = "/api/v1/memory/sync/summary"
payload = f"{ts}|GET|{path}".encode()
sig = encode_multibase(sign_bytes(payload, kp.private_seed))
req = urllib.request.Request(
    f"http://localhost:{port}{path}",
    headers={
        "X-Peer-DID": did,
        "X-Peer-Timestamp": ts,
        "X-Peer-Signature": sig,
        "Accept": "application/json",
    },
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        sys.stdout.write(resp.read().decode())
except Exception as exc:
    sys.stdout.write(json.dumps({"error": str(exc)}))
PY
  else
    fed::warn "python3 not found — cannot sign peer-auth request; falling back to Merkle-root proxy via /ready"
    curl -sS --max-time 5 "http://localhost:${port}/ready" || true
  fi
}

fed::merkle_root() {
  local node="$1"
  local body; body=$(fed::sync_summary "$node")
  printf '%s' "$body" | fed::jq_or '.root // empty'
}

fed::merkle_record_count() {
  local node="$1"
  local body; body=$(fed::sync_summary "$node")
  printf '%s' "$body" | fed::jq_or '.record_count // 0'
}

fed::wait_for_health() {
  local node="$1"; local timeout="${2:-60}"
  local port; port=$(fed::port_for "$node") || fed::die "unknown node '$node'"
  local deadline=$((SECONDS + timeout))
  while [ $SECONDS -lt $deadline ]; do
    if curl -sS --max-time 3 "http://localhost:${port}/health" >/dev/null 2>&1; then
      fed::ok "$node healthy (port $port)"
      return 0
    fi
    sleep 1
  done
  fed::die "$node did not become healthy within ${timeout}s"
}

fed::seed_workflow() {
  local node="$1"
  local body="{\"workflow_id\": \"$FED_DEMO_WORKFLOW_ID\", \"prompt\": \"federation demo\", \"user_id\": \"demo-user\"}"
  fed::http_post "$node" "/api/v1/demo/seed-workflow" "$body" >/dev/null
  fed::info "  seeded workflow on $node"
}

fed::json_string() {
  # Escape a shell argument into a JSON string literal without needing jq.
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json, sys; sys.stdout.write(json.dumps(sys.argv[1]))' "$1"
  else
    printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\x08/\\b/g' -e 's/\t/\\t/g' -e 's/\r/\\r/g' -e 's/\n/\\n/g')"
  fi
}

fed::pretty_json() {
  if command -v jq >/dev/null 2>&1; then
    jq '.'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m json.tool
  else
    cat
  fi
}

fed::jq_or() {
  # jq '<expr>' if available; simple Python fallback on a dict key otherwise.
  local expr="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -r "$expr"
    return
  fi
  python3 - "$expr" <<'PY'
import json, re, sys
expr = sys.argv[1]
data = json.loads(sys.stdin.read() or '{}')
# Very small subset: '.field // empty', '.field // 0', or '.field.sub'.
m = re.match(r"\.([\w.]+)\s*//\s*(.*)", expr) or re.match(r"\.([\w.]+)", expr)
if not m:
    sys.stdout.write("")
    sys.exit(0)
path = m.group(1).split('.')
fallback = (m.group(2) if m.lastindex and m.lastindex > 1 else "").strip()
if fallback in ("empty",):
    fallback = ""
if fallback and fallback[0] in "0123456789":
    fallback = fallback
ptr = data
for p in path:
    if isinstance(ptr, dict) and p in ptr:
        ptr = ptr[p]
    else:
        ptr = None
        break
sys.stdout.write(str(ptr) if ptr is not None else fallback)
PY
}

# Export helpers for sub-shells (demo_federation_test.sh fans out to
# sub-scripts).
export FED_REPO_ROOT FED_COMPOSE_FILE FED_NETWORK
export FED_PORT_A FED_PORT_B FED_PORT_C FED_DEMO_WORKFLOW_ID
