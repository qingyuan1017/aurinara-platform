#!/usr/bin/env bash
set -euo pipefail

workspace_dir=${WORKSPACE_DIR:-/workspace}
session_seconds=${WORKSPACE_SESSION_SECONDS:-7200}
ready_file="$workspace_dir/.aurinara-ready"

mkdir -p \
  "$workspace_dir/.aurinara/code-server/user-data" \
  "$workspace_dir/.aurinara/code-server/extensions"

# Do not expose the editor until the sync sidecar has completed its initial
# restore attempt. The sidecar creates the marker even when restore fails so a
# recoverable S3 outage does not leave the task stuck forever.
for _ in $(seq 1 300); do
  [[ -f "$ready_file" ]] && break
  sleep 1
done
[[ -f "$ready_file" ]] || exit 1

exec timeout --foreground --signal=TERM --kill-after=120 "$session_seconds" \
  code-server \
    --bind-addr 0.0.0.0:8080 \
    --auth none \
    --disable-telemetry \
    --user-data-dir "$workspace_dir/.aurinara/code-server/user-data" \
    --extensions-dir "$workspace_dir/.aurinara/code-server/extensions" \
    "$workspace_dir"
