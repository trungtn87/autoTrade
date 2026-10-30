# Production cutover checklist

Do not enable live trading until every mandatory item is evidenced and the Owner explicitly approves the cutover.

## Current source and legacy platform

- [ ] Bot commit is `dc554f782668fa03b8f4f212feaa66bdba153c1f`.
- [ ] Dashboard commit is `9bdc9b0f4b32d98fee7c9ffa37b3dbbb43fe6308`.
- [ ] Render `autotrade-v2-shadow` automatic deploy is disabled.
- [ ] Render `autotrade-v2-shadow` is explicitly suspended and cannot resume after billing/free-limit changes.
- [ ] Render `autotrade-live-dashboard` scheduler is stopped before the VPS collector becomes authoritative.
- [ ] No scheduled GitHub Action invokes the collector (manual dispatch may remain for emergency backfill).
- [ ] No other legacy service (`autoTrade`, `autoTrade-1`, or another host) has trading credentials and an active scheduler.

Render was observed on 2026-10-30 with both named services billing-suspended but `autoDeploy=yes`. Suspension alone is not a permanent anti-duplication control.

## Backup and exchange state

- [ ] `backup-state.sh pre-migration` completed and the backup path was recorded.
- [ ] Supabase row counts/latest candle timestamps were recorded without changing data.
- [ ] Current BingX positions and exact entry/SL/TP/trailing order IDs were recorded read-only.
- [ ] No migration step cancelled or replaced an existing BingX position or protection order.
- [ ] Local persistent `STATE_DB` is under `/var/lib/autotrade/bot`, not inside a release.

## VPS acceptance

- [ ] SSH key authentication works; password authentication is disabled only after key verification.
- [ ] UFW exposes only SSH and Nginx; ports 8101/8102 are loopback-only.
- [ ] Dashboard certificate is valid and HTTPS health succeeds.
- [ ] Both services recover after a VPS reboot.
- [ ] A forced service failure is restarted by systemd and emits a non-blocking alert.
- [ ] Journal size/retention limits are active.
- [ ] Rollback to a prior release was tested without changing Supabase.

## Application acceptance in safe mode

- [ ] Bot health: `execution_enabled=false`, `dry_run=true`.
- [ ] BTC-USDT and ETH-USDT 15m stores are healthy with 3400 candles.
- [ ] WebSocket reconnect was exercised.
- [ ] Missing candle REST recovery stayed within its existing request budget.
- [ ] A Supabase outage failed closed; no signal/order was emitted from incomplete data.
- [ ] Startup/recovery/cleanup made no BingX mutations in safe mode.
- [ ] Restart did not create a duplicate entry or reopen a terminal event.
- [ ] An unfinished setup fixture reconciled idempotently in offline tests.
- [ ] Hard SL, 50% hard TP, 50% trailing, 0.5% activation, and 0.5% callback remain unchanged.
- [ ] Dashboard displays combos, PnL, statistics, and trade history.
- [ ] Dashboard sync ran at Vietnam-time `:07` with read-only credentials.
- [ ] Discord failure did not block bot processing.

## Approval and activation

- [ ] Test results, health output, service status, dashboard URL, missing variable names, and blockers were reported to the Owner.
- [ ] Owner approval reference: ____________________
- [ ] Approval timestamp (UTC): ____________________
- [ ] `/etc/autotrade/LIVE_APPROVED` contains that reference and timestamp.
- [ ] Render bot is still disabled immediately before activation.
- [ ] Exactly one live bot exists immediately after activation.

Stop activation and restore safe flags if there is any risk of a duplicate entry, missing SL, ambiguous stored order, or unsafe recovery.

