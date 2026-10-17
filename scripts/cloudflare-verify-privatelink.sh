#!/usr/bin/env bash
set -euo pipefail
stack_name=${1:?usage: cloudflare-verify-privatelink.sh STACK_NAME}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${CLOUDFLARE_API_TOKEN:?}"
: "${CLOUDFLARE_ZONE_ID:?}"
cd "$(dirname "$script_dir")"
exec python3 -m scripts.cloudflare_private_dns "$stack_name"
