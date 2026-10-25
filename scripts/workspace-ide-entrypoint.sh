#!/usr/bin/env bash
set -euo pipefail

workspace_dir=${WORKSPACE_DIR:-/workspace}
state_dir=${WORKSPACE_STATE_DIR:-$workspace_dir/.aurinara}
session_seconds=${WORKSPACE_SESSION_SECONDS:-7200}
wait_for_sync=${WORKSPACE_WAIT_FOR_SYNC:-true}
ready_file="$workspace_dir/.aurinara-ready"
legacy_code_server_dir="$workspace_dir/.aurinara/code-server"

# Older workspace images kept editor state inside the visible workspace. Move
# it once to the private state mount so existing settings/extensions survive.
if [[ "$state_dir" != "$workspace_dir/.aurinara" && -d "$legacy_code_server_dir" ]]; then
  mkdir -p "$state_dir/code-server"
  cp -a "$legacy_code_server_dir/." "$state_dir/code-server/"
  rm -rf -- "$legacy_code_server_dir"
fi

mkdir -p \
  "$state_dir/code-server/user-data" \
  "$state_dir/code-server/extensions"

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
    --user-data-dir "$state_dir/code-server/user-data" \
    --extensions-dir "$state_dir/code-server/extensions" \
    "$workspace_dir"
