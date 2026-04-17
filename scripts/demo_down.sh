#!/usr/bin/env bash
# demo_down.sh — tear the federation down and remove its containers + network.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib/federation.sh
. "$SCRIPT_DIR/lib/federation.sh"

fed::require_docker

fed::info "stopping and removing the federation stack"
(
  cd "$FED_REPO_ROOT"
  docker compose -f "$FED_COMPOSE_FILE" down --remove-orphans
)
fed::ok "federation torn down."
