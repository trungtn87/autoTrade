#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
domain=${1:-}
email=${2:-}
[[ $domain =~ ^[A-Za-z0-9.-]+$ && $domain == *.* ]] || {
  echo "Usage: configure-https.sh DOMAIN EMAIL" >&2; exit 2;
}
[[ $email == *@* ]] || { echo "A valid certificate email is required" >&2; exit 2; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
template="$SCRIPT_DIR/../nginx/autotrade.conf.template"
temporary=/etc/nginx/sites-available/autotrade-http
final=/etc/nginx/sites-available/autotrade

sed -e "s/__DOMAIN__/$domain/g" "$template" >"$final.pending"
cat >"$temporary" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $domain;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 503; }
}
EOF
ln -sfn "$temporary" /etc/nginx/sites-enabled/autotrade
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

certbot certonly --webroot -w /var/www/html -d "$domain" \
  --non-interactive --agree-tos --email "$email"
mv "$final.pending" "$final"
ln -sfn "$final" /etc/nginx/sites-enabled/autotrade
nginx -t
systemctl reload nginx
systemctl enable --now certbot.timer
curl --fail --silent --show-error --max-time 15 "https://$domain/health" >/dev/null
echo "Dashboard HTTPS ready at https://$domain/"

