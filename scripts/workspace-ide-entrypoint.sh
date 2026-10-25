#!/usr/bin/env bash
set -euo pipefail

workspace_dir=${WORKSPACE_DIR:-/workspace}
session_seconds=${WORKSPACE_SESSION_SECONDS:-7200}
wait_for_sync=${WORKSPACE_WAIT_FOR_SYNC:-true}
ready_file="$workspace_dir/.aurinara-ready"

mkdir -p \
  "$workspace_dir/.aurinara/code-server/user-data" \
  "$workspace_dir/.aurinara/code-server/extensions"

# ECS waits for the sync sidecar's initial restore. Static local development
# uses a named volume directly and intentionally has no sync sidecar.
if [[ "$wait_for_sync" == true ]]; then
  for _ in $(seq 1 300); do
    [[ -f "$ready_file" ]] && break
    sleep 1
  done
  [[ -f "$ready_file" ]] || exit 1
fi

exec timeout --foreground --signal=TERM --kill-after=120 "$session_seconds" \
  code-server \
    --bind-addr 0.0.0.0:8080 \
    --auth none \
    --disable-telemetry \
    --user-data-dir "$workspace_dir/.aurinara/code-server/user-data" \
    --extensions-dir "$workspace_dir/.aurinara/code-server/extensions" \
    "$workspace_dir"
