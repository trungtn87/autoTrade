#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEPLOY_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck disable=SC1091
. "$DEPLOY_DIR/versions.env"

BOT_COMMIT=${BOT_COMMIT_OVERRIDE:-$BOT_COMMIT}
DASHBOARD_COMMIT=${DASHBOARD_COMMIT_OVERRIDE:-$DASHBOARD_COMMIT}
[[ $BOT_COMMIT =~ ^[0-9a-f]{40}$ && $DASHBOARD_COMMIT =~ ^[0-9a-f]{40}$ ]] || {
  echo "Both commits must be full 40-character SHAs" >&2; exit 1;
}

require_value() {
  local file=$1 key=$2 value
  value=$(sed -n "s/^${key}=//p" "$file" | tail -n1)
  [[ -n $value ]] || { echo "Missing ${key} in ${file}" >&2; exit 1; }
}
env_value() { sed -n "s/^$2=//p" "$1" | tail -n1; }

BOT_ENV=/etc/autotrade/bot.env
DASH_ENV=/etc/autotrade/dashboard.env
[[ -r $BOT_ENV && -r $DASH_ENV ]] || { echo "Install both environment files first" >&2; exit 1; }
require_value "$BOT_ENV" DATABASE_URL
require_value "$BOT_ENV" BINGX_API_KEY
require_value "$BOT_ENV" BINGX_API_SECRET
require_value "$DASH_ENV" DATABASE_URL
require_value "$DASH_ENV" BINGX_READ_API_KEY
require_value "$DASH_ENV" BINGX_READ_API_SECRET

execution=$(env_value "$BOT_ENV" EXECUTION_ENABLED | tr '[:upper:]' '[:lower:]')
dry_run=$(env_value "$BOT_ENV" DRY_RUN | tr '[:upper:]' '[:lower:]')
if [[ $execution == true && $dry_run == false && ! -f /etc/autotrade/LIVE_APPROVED ]]; then
  echo "Refusing live execution: /etc/autotrade/LIVE_APPROVED is absent" >&2
  exit 1
fi

REPO=/opt/autotrade/repository.git
if [[ ! -d $REPO ]]; then
  sudo -u autotrade git clone --mirror "$REPOSITORY_URL" "$REPO"
else
  sudo -u autotrade git --git-dir="$REPO" fetch --prune origin
fi
for sha in "$BOT_COMMIT" "$DASHBOARD_COMMIT"; do
  sudo -u autotrade git --git-dir="$REPO" cat-file -e "${sha}^{commit}"
done

release_id="$(date -u +%Y%m%dT%H%M%SZ)-${BOT_COMMIT:0:8}-${DASHBOARD_COMMIT:0:8}"
release_root="/opt/autotrade/releases/$release_id"
install -d -o autotrade -g autotrade -m 0750 "$release_root"
sudo -u autotrade git --git-dir="$REPO" worktree add --detach "$release_root/bot" "$BOT_COMMIT"
sudo -u autotrade git --git-dir="$REPO" worktree add --detach "$release_root/dashboard" "$DASHBOARD_COMMIT"

sudo -u autotrade python3 -m venv "$release_root/bot/.venv"
sudo -u autotrade "$release_root/bot/.venv/bin/pip" install --disable-pip-version-check \
  -r "$release_root/bot/autotrade_v2/requirements.txt"
sudo -u autotrade python3 -m venv "$release_root/dashboard/.venv"
sudo -u autotrade "$release_root/dashboard/.venv/bin/pip" install --disable-pip-version-check \
  -r "$release_root/dashboard/dashboard/requirements.txt"

sudo -u autotrade "$release_root/bot/.venv/bin/python" -m compileall -q "$release_root/bot/autotrade_v2/app"
sudo -u autotrade "$release_root/dashboard/.venv/bin/python" -m compileall -q "$release_root/dashboard/dashboard/autotrade_live"
printf '%s\n' "$BOT_COMMIT" >"$release_root/BOT_COMMIT"
printf '%s\n' "$DASHBOARD_COMMIT" >"$release_root/DASHBOARD_COMMIT"
chown autotrade:autotrade "$release_root/BOT_COMMIT" "$release_root/DASHBOARD_COMMIT"

ln -sfn "$release_root/bot" /opt/autotrade/current-bot.new
ln -sfn "$release_root/dashboard" /opt/autotrade/current-dashboard.new
mv -Tf /opt/autotrade/current-bot.new /opt/autotrade/current-bot
mv -Tf /opt/autotrade/current-dashboard.new /opt/autotrade/current-dashboard

systemctl daemon-reload
systemctl restart autotrade-bot.service autotrade-dashboard.service
systemctl enable --now autotrade-health.timer
sleep 3
"$SCRIPT_DIR/preflight.sh" --local

echo "Release installed: $release_id"
echo "Bot:       $BOT_COMMIT"
echo "Dashboard: $DASHBOARD_COMMIT"
