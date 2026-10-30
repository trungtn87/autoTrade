#!/usr/bin/env bash
set -Eeuo pipefail

mode=${1:---local}
[[ $mode == --local ]] || { echo "Only --local is supported" >&2; exit 2; }

bot_json=$(curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8101/health)
dashboard_json=$(curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8102/health)

approved=false
[[ -f /etc/autotrade/LIVE_APPROVED ]] && approved=true
python3 - "$bot_json" "$dashboard_json" "$approved" <<'PY'
import json, sys

bot = json.loads(sys.argv[1])
dashboard = json.loads(sys.argv[2])
approved = sys.argv[3] == "true"
errors = []
if not bot.get("ok"):
    errors.append("bot health is not ok")
safe = bot.get("execution_enabled") is False and bot.get("dry_run") is True
live = bot.get("execution_enabled") is True and bot.get("dry_run") is False
if not safe and not (approved and live):
    errors.append("execution flags are neither safe nor explicitly approved")
if not dashboard.get("ok"):
    errors.append("dashboard health is not ok")
if dashboard.get("trade_api") is not False:
    errors.append("dashboard unexpectedly reports a trade API")
if errors:
    raise SystemExit("; ".join(errors))
print("Local health checks passed in approved live mode." if live else
      "Local health checks passed in non-trading mode.")
PY

systemctl is-active --quiet autotrade-bot.service
systemctl is-active --quiet autotrade-dashboard.service
systemctl is-enabled --quiet autotrade-bot.service
systemctl is-enabled --quiet autotrade-dashboard.service
