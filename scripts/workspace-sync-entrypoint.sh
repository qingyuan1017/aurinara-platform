#!/usr/bin/env bash
set -euo pipefail

workspace_dir=${WORKSPACE_DIR:-/workspace}
sync_interval=${WORKSPACE_SYNC_INTERVAL_SECONDS:-60}

: "${WORKSPACE_ID:?WORKSPACE_ID is required}"
: "${WORKSPACE_BUCKET:?WORKSPACE_BUCKET is required}"
: "${WORKSPACE_PREFIX:?WORKSPACE_PREFIX is required}"
: "${WORKSPACE_QUOTA_BYTES:?WORKSPACE_QUOTA_BYTES is required}"
: "${WORKSPACE_CALLBACK_URL:?WORKSPACE_CALLBACK_URL is required}"
: "${WORKSPACE_CALLBACK_TOKEN:?WORKSPACE_CALLBACK_TOKEN is required}"

mkdir -p "$workspace_dir/.aurinara"

usage_bytes() {
  du -sb "$workspace_dir" | awk '{print $1}'
}

heartbeat() {
  local state=$1
  local bytes=$2
  local failure=${3:-}
  local synced_at=null
  if [[ "$state" == synced ]]; then synced_at="\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""; fi
  local failure_json=null
  if [[ -n "$failure" ]]; then failure_json="\"$failure\""; fi
  printf '{"event":"workspace_sync","workspace_id":"%s","state":"%s","usage_bytes":%s,"failure_code":%s}\n' \
    "$WORKSPACE_ID" "$state" "$bytes" "$failure_json"
  curl --fail --silent --show-error --max-time 10 \
    -X POST \
    -H 'Content-Type: application/json' \
    -H "X-Workspace-Token: ${WORKSPACE_CALLBACK_TOKEN}" \
    --data "{\"sync_state\":\"${state}\",\"usage_bytes\":${bytes},\"synced_at\":${synced_at},\"failure_code\":${failure_json}}" \
    "$WORKSPACE_CALLBACK_URL" >/dev/null || true
}

sync_to_s3() {
  local bytes
  bytes=$(usage_bytes)
  if (( bytes > WORKSPACE_QUOTA_BYTES )); then
    cat > "$workspace_dir/.aurinara/QUOTA_EXCEEDED.md" <<'EOF'
# Workspace storage limit exceeded

Reduce this workspace below 5 GB. Cloud persistence and normal Stop remain paused until then.
EOF
    heartbeat quota_exceeded "$bytes" quota_exceeded
    return 1
  fi
  rm -f "$workspace_dir/.aurinara/QUOTA_EXCEEDED.md"
  heartbeat syncing "$bytes"
  if aws s3 sync "$workspace_dir" "s3://${WORKSPACE_BUCKET}/${WORKSPACE_PREFIX}" \
    --delete --exclude '.aurinara-ready'; then
    heartbeat synced "$(usage_bytes)"
    return 0
  fi
  heartbeat failed "$bytes" sync_failed
  return 1
}

shutdown() {
  trap - TERM INT
  sync_to_s3 || true
  exit 0
}
trap shutdown TERM INT

heartbeat syncing 0
if aws s3 sync "s3://${WORKSPACE_BUCKET}/${WORKSPACE_PREFIX}" "$workspace_dir" \
  --delete --exclude '.aurinara-ready'; then
  initial_bytes=$(usage_bytes)
  touch "$workspace_dir/.aurinara-ready"
  if (( initial_bytes > WORKSPACE_QUOTA_BYTES )); then
    heartbeat quota_exceeded "$initial_bytes" quota_exceeded
  else
    heartbeat synced "$initial_bytes"
  fi
else
  touch "$workspace_dir/.aurinara-ready"
  heartbeat failed 0 sync_failed
fi

while true; do
  sleep "$sync_interval" &
  wait $! || true
  sync_to_s3 || true
done
