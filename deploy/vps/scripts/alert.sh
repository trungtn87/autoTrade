#!/usr/bin/env bash
set -u

unit=${1:-unknown}
webhook=${DISCORD_WEBHOOK_ERROR:-}
[[ -n $webhook ]] || exit 0
host=$(hostname -f 2>/dev/null || hostname)
payload=$(python3 - "$unit" "$host" <<'PY'
import json, sys
print(json.dumps({"content": f"AUTOTRADE ALERT: {sys.argv[1]} failed on {sys.argv[2]}"}))
PY
)
curl --fail --silent --show-error --max-time 10 \
  -H 'Content-Type: application/json' --data "$payload" "$webhook" >/dev/null || true

