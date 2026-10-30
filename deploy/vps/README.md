# Dedicated VPS deployment

This directory installs the existing production Autotrade bot and dashboard on one Ubuntu 24.04 VPS without changing trading logic. The releases are pinned to the commits that Render reported as live on 2026-10-30:

- Bot (`autotrade-v2-clean`): `dc554f782668fa03b8f4f212feaa66bdba153c1f`
- Dashboard (`main`): `9bdc9b0f4b32d98fee7c9ffa37b3dbbb43fe6308`

The first deployment is forced into `EXECUTION_ENABLED=false` and `DRY_RUN=true`. A live configuration is refused unless the Owner has deliberately created `/etc/autotrade/LIVE_APPROVED`. Do not create that marker during installation or pre-cutover checks.

## Layout

```text
/opt/autotrade/repository.git       shared read-only Git mirror
/opt/autotrade/releases/*           immutable, SHA-pinned releases
/opt/autotrade/current-*            atomic symlinks to active releases
/etc/autotrade/*.env                secrets and runtime switches (not in Git)
/var/lib/autotrade/bot              persistent SQLite recovery state
/var/backups/autotrade              local state and Supabase logical backups
```

The bot and dashboard have separate virtual environments and systemd units. Uvicorn binds only to `127.0.0.1`; Nginx is the only public listener. The dashboard collector uses separate `BINGX_READ_API_KEY` and `BINGX_READ_API_SECRET` credentials and cannot submit orders.

## Owner inputs required

Before deployment, provide:

- VPS IPv4/IPv6 address, an SSH username with sudo, and the SSH private key or authorized public key.
- A dashboard DNS name whose A/AAAA record points to the VPS, plus a certificate email.
- The existing Supabase `DATABASE_URL` (same database; no schema reset).
- Bot BingX credentials and separate read-only dashboard BingX credentials.
- Discord webhooks and dashboard authentication secrets.

Never paste secret values into an issue, PR, command transcript, or log.

## Install

Copy this directory to the VPS, then run as root:

```bash
deploy/vps/scripts/bootstrap-ubuntu.sh
sudoedit /etc/autotrade/bot.env
sudoedit /etc/autotrade/dashboard.env
deploy/vps/scripts/backup-state.sh pre-migration
deploy/vps/scripts/deploy-release.sh
deploy/vps/scripts/configure-https.sh dashboard.example.com owner@example.com
```

Keep these values during all validation:

```text
EXECUTION_ENABLED=false
DRY_RUN=true
```

`deploy-release.sh` installs both exact commits, switches both symlinks atomically, restarts the units, and verifies local health. It does not modify Supabase data or BingX orders.

## Validation

```bash
systemctl status autotrade-bot autotrade-dashboard --no-pager
systemctl status autotrade-health.timer --no-pager
journalctl -u autotrade-bot -u autotrade-dashboard --since '15 minutes ago' --no-pager
deploy/vps/scripts/preflight.sh --local
curl --fail https://dashboard.example.com/health
```

Confirm the bot health response shows `execution_enabled=false`, `dry_run=true`, both symbols healthy, WebSocket connected, and Supabase as the backend. Confirm dashboard health shows `trade_api=false` and the hourly archive scheduler enabled. Wait through a Vietnam-time `:07` run and verify one successful sync without abnormal BingX request growth.

Reboot acceptance test:

```bash
reboot
# reconnect after boot
systemctl is-active autotrade-bot autotrade-dashboard
deploy/vps/scripts/preflight.sh --local
```

## Update and rollback

Edit only the full SHAs in `versions.env` (or use the two documented `*_COMMIT_OVERRIDE` environment variables), rerun `deploy-release.sh`, and record both SHAs in the change ticket. Each deployment retains the prior release.

```bash
ls -1 /opt/autotrade/releases
deploy/vps/scripts/rollback.sh RELEASE_ID
```

Rollback changes application symlinks only. It first backs up local state and never rolls back, deletes, or resets Supabase. Validate schema compatibility before rolling code backward.

## Cutover gate

Complete `CUTOVER.md` and obtain explicit Owner approval. Before live activation, Render automatic deploys must be disabled and the old bot must be suspended by the Owner; GitHub Actions must remain manual-only. Verify BingX positions and existing SL/TP/trailing orders without cancelling them.

Only after approval:

1. Record the approval and current BingX/Supabase backup evidence.
2. Create root-owned `/etc/autotrade/LIVE_APPROVED` with the approval identifier and timestamp.
3. Change `EXECUTION_ENABLED=true` and `DRY_RUN=false` in `/etc/autotrade/bot.env`.
4. Restart only `autotrade-bot` and immediately verify `/health`, recovery logs, open positions, and protection orders.

Remove the marker and restore safe flags before investigating any duplicate-entry, missing-SL, or ambiguous recovery warning.

