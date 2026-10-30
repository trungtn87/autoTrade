#!/usr/bin/env bash
set -u

state_dir=/var/lib/autotrade/health
mkdir -p "$state_dir"
failed=0
for item in bot:8101 dashboard:8102; do
  name=${item%%:*}
  port=${item##*:}
  marker="$state_dir/$name.failed"
  if curl --fail --silent --show-error --max-time 8 "http://127.0.0.1:${port}/health" >/dev/null; then
    rm -f "$marker"
  else
    failed=1
    systemctl try-restart "autotrade-${name}.service" || true
    if [[ ! -e $marker ]]; then
      touch "$marker"
      /usr/local/lib/autotrade/alert.sh "autotrade-${name}-health" || true
    fi
  fi
done
exit "$failed"

