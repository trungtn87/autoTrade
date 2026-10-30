#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
target=${1:-}
[[ -n $target ]] || { echo "Usage: rollback.sh RELEASE_ID" >&2; exit 2; }
[[ $target != */* && $target != *..* ]] || { echo "Invalid release ID" >&2; exit 2; }
release_root="/opt/autotrade/releases/$target"
[[ -d $release_root/bot && -d $release_root/dashboard ]] || {
  echo "Release not found: $target" >&2; exit 1;
}

/usr/local/lib/autotrade/backup-state.sh "pre-rollback-$target"
ln -sfn "$release_root/bot" /opt/autotrade/current-bot.new
ln -sfn "$release_root/dashboard" /opt/autotrade/current-dashboard.new
mv -Tf /opt/autotrade/current-bot.new /opt/autotrade/current-bot
mv -Tf /opt/autotrade/current-dashboard.new /opt/autotrade/current-dashboard
systemctl restart autotrade-bot.service autotrade-dashboard.service
sleep 3
"$(dirname -- "$0")/preflight.sh" --local
echo "Rolled back application code to $target; Supabase and local state were not rolled back."

