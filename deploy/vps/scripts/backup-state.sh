#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

label=${1:-manual}
safe_label=$(printf '%s' "$label" | tr -cd 'A-Za-z0-9._-')
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/var/backups/autotrade/${stamp}-${safe_label:-backup}"
install -d -o root -g autotrade -m 0750 "$backup_dir"

state_db=$(sed -n 's/^STATE_DB=//p' /etc/autotrade/bot.env | tail -n1)
if [[ -n $state_db && -f $state_db ]]; then
  sqlite3 "$state_db" ".backup '$backup_dir/autotrade_v2.db'"
  chmod 0640 "$backup_dir/autotrade_v2.db"
fi

database_url=$(sed -n 's/^DATABASE_URL=//p' /etc/autotrade/bot.env | tail -n1)
if [[ -n $database_url ]]; then
  PGCONNECT_TIMEOUT=15 PGDATABASE="$database_url" \
    pg_dump --no-owner --no-privileges --format=custom \
    --file="$backup_dir/supabase.dump"
  chmod 0640 "$backup_dir/supabase.dump"
fi

if [[ -d /opt/autotrade/current-bot ]]; then
  git -C /opt/autotrade/current-bot rev-parse HEAD >"$backup_dir/bot.commit"
fi
if [[ -d /opt/autotrade/current-dashboard ]]; then
  git -C /opt/autotrade/current-dashboard rev-parse HEAD >"$backup_dir/dashboard.commit"
fi
if compgen -G "$backup_dir/*.commit" >/dev/null; then
  chmod 0640 "$backup_dir/"*.commit
fi
echo "$backup_dir"
