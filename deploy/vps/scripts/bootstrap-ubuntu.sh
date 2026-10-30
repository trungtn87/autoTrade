#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -f /etc/os-release ]] || { echo "Unsupported host" >&2; exit 1; }
. /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || {
  echo "Expected Ubuntu 24.04; found ${PRETTY_NAME:-unknown}" >&2
  exit 1
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEPLOY_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates certbot curl git nginx python3 python3-venv python3-pip \
  postgresql-client rsync sqlite3 ufw

if ! id autotrade >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/autotrade --shell /usr/sbin/nologin autotrade
fi

install -d -o autotrade -g autotrade -m 0750 \
  /opt/autotrade /opt/autotrade/releases /var/lib/autotrade/bot /var/lib/autotrade/health
install -d -o root -g autotrade -m 0750 /etc/autotrade
install -d -o root -g root -m 0755 /usr/local/lib/autotrade

for env_name in bot dashboard; do
  target="/etc/autotrade/${env_name}.env"
  if [[ ! -e $target ]]; then
    install -o root -g autotrade -m 0640 "$DEPLOY_DIR/env/${env_name}.env.example" "$target"
  fi
done

install -o root -g root -m 0755 "$SCRIPT_DIR/alert.sh" /usr/local/lib/autotrade/alert.sh
install -o root -g root -m 0755 "$SCRIPT_DIR/healthcheck.sh" /usr/local/lib/autotrade/healthcheck.sh
install -o root -g root -m 0755 "$SCRIPT_DIR/backup-state.sh" /usr/local/lib/autotrade/backup-state.sh
install -o root -g root -m 0644 "$DEPLOY_DIR/systemd/"*.service /etc/systemd/system/
install -o root -g root -m 0644 "$DEPLOY_DIR/systemd/"*.timer /etc/systemd/system/

install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
cat >/etc/systemd/journald.conf.d/autotrade-limits.conf <<'EOF'
[Journal]
SystemMaxUse=250M
RuntimeMaxUse=100M
MaxRetentionSec=14day
EOF

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# Disable password login only when the administrator running this script has a key.
admin_user=${SUDO_USER:-root}
admin_home=$(getent passwd "$admin_user" | cut -d: -f6)
authorized_keys="${admin_home:-/root}/.ssh/authorized_keys"
if [[ -s $authorized_keys ]]; then
  install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
  cat >/etc/ssh/sshd_config.d/60-autotrade-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
EOF
  sshd -t
  systemctl reload ssh
else
  echo "WARNING: SSH password authentication left unchanged because no authorized_keys file was found." >&2
fi

systemctl daemon-reload
systemctl restart systemd-journald
systemctl enable autotrade-bot.service autotrade-dashboard.service autotrade-health.timer

echo "Bootstrap complete. Fill /etc/autotrade/*.env, then run deploy-release.sh."
