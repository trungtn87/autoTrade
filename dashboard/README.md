# Autotrade Live Dashboard (BingX USDT Perpetual)

A standalone **read-only** analytics package for **real BingX futures executions**. Existing Autotrade trading and TradingView backtest workflows are not modified.

## Scope & data guarantees

- Fetches BingX **historical orders**, **filled executions**, **income / funding / fees**, **forced liquidation & ADL**, and **closed-position history**.
- Stores raw API payloads **and** normalized fields in SQLite (local development) or persistent PostgreSQL (production).
- Upserts using orderId / tradeId / income key, supports repeated polling and daily backfills. Saturated unpaginated endpoints are split into smaller time windows; paginated fills/positions are fetched page by page.
- Supports linking `entry_order_id → combo` (preferred: event from Autotrade after entry order confirmed; fallback: dashboard CSV/manual).
- Reconstructs position rounds from fills; a session with multiple/unknown entries is **UNMAPPED/MIXED**, not falsely attributed to a Combo.
- Shows realized PnL (filled trade `realizedPnl + fee`), win rate and profit factor from **closed position rounds**, plus liquidation/ADL events and data quality issues.
- **Does not** place, modify, cancel, or close any order. It has no POST/DELETE BingX trading calls.

### Important limits

1. **BingX does not know Autotrade Combo.** Without a mapping captured at placement time, the dashboard cannot calculate trustworthy per-Combo statistics. No automatic time-based guessing.
2. A *filled order* is not the same as a *complete trading position*. The ledger groups compatible opening and closing fills until flat. Simultaneous Combo entries into the same symbol + side are shown as MIXED; complex hedging / reversed positions require broker-level reconciliation.
3. **Funding is stored separately**, not allocated to a specific Combo unless it can be reconciled to a position. A gross account-level PnL metric includes fill fees, but not funding. Do not add the income table's `REALIZED_PNL` and `TRADING_FEE` to fills a second time.
4. The dashboard uses BingX `fee` sign conventions (documented paid fees negative); spot-check first few fills on your account. Liquidation flags use `forceOrders`, and full loss reconciliation may also require `INSURANCE_CLEAR` income.
5. Historical availability is limited by BingX. `user/income` retains only about three months. Backfill often; do not assume you can rebuild older records.
6. API response fields and historical retention can change. This package was validated with **synthetic offline records**, not your live API key. The initial real sync should be verified against BingX exported transaction history.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
# Copy .env.example -> .env, then EXPORT the variables into the shell or your hosting env.
export BINGX_READ_API_KEY='...'
export BINGX_READ_API_SECRET='...'
export BINGX_SYMBOLS='BTC-USDT,ETH-USDT'
export DASHBOARD_PASSWORD='choose-a-long-secret'
export DATABASE_URL='sqlite:///autotrade_live.db'

python -m autotrade_live.sync --days 7
streamlit run dashboard.py
```

**Running Streamlit:** use `streamlit run dashboard.py` from the project root. The launcher re-executes the dashboard on each Streamlit rerun.

To automatically load `.env` locally, use your shell's dotenv loader or copy environment variables into the session; `.env` is ignored in Git.

## Database & scheduled sync (production)

Use hosted **PostgreSQL** (e.g., Render Postgres or Supabase), with `DATABASE_URL=postgresql://...`, preferably using TLS. Do **not** store a production database on Render's ephemeral local disk.

Deploy three **separate components** sharing the same database URL:

1. **Scheduled sync worker**: `python -m autotrade_live.sync --days 1` every 5-15 minutes. Backfill once with `--days 30` / `--days 90` as needed. Overlap is intentional; unique keys prevent duplication. The actual scheduler must remain active on your hosting plan (Render cron job, scheduler service, or external cron).
2. **Dashboard web service**: `streamlit run dashboard.py --server.address 0.0.0.0 --server.port $PORT --server.headless true` with `DASHBOARD_PASSWORD` and database URL. Use platform SSO / private networking in addition to the built-in basic password gate where possible.
3. **Optional Combo tag receiver**: `uvicorn autotrade_live.ingest:app --host 0.0.0.0 --port $PORT` with a **separate strong COLLECTOR_TOKEN**. Autotrade needs only the receiver URL and this token, not database access. When a confirmed entry order returns orderId, use the example `record_combo_async(...)` hook (only after reviewing/merging into the actual trading bot). Receiver returns HTTP 202, never accepts trading instructions.

On Render, dashboard and receiver may need separate web services. No deployment is included with this package and no BingX credentials are embedded.

## Attaching Combo labels

Preferred once live integration is enabled:

```http
POST /v1/signals
X-Collector-Token: YOUR_RANDOM_SECRET
Content-Type: application/json

{"entry_order_id":"1234567890123456789", "combo":"COMBO_5_15M", "signal_id":"alert-...", "symbol":"BTC-USDT", "side":"BUY"}
```

`entry_order_id` is a **string**, preventing loss of precision for BingX's large IDs. The receiver rejects conflicting reassignment of the same entry to a different Combo. A lost request can be replayed safely or imported as CSV later. See `example_autotrade_hook.py` for an illustrative asynchronous hook. A durable retry queue is recommended before full live reliance.

CSV header: `entry_order_id,combo,signal_id,symbol,side,signal_time_ms`. The dashboard accepts only order IDs already in stored BingX history for manual/CSV assignment. Use the `Gắn Combo` tab. By design, entries without labels do not appear as attributable Combo performance.

## Dashboard sections

- **Tổng quan**: Fill-level realized PnL, attributable win rate, closed rounds, unmapped rounds, liquidation count, cumulative PnL.
- **Theo Combo**: Number of completed sessions, win rate, net PnL, profit factor, liquidation count.
- **Nhật ký**: Completed and outstanding sessions with partial exit type, fees, order IDs; downloadable CSV.
- **Đối soát**: Orders, fills, income, force-close flags, collector health and unresolved position matches.
- **Gắn Combo**: Enter/batch-import mapping between BingX entry orderId and strategy name.

## Known future improvements

- API-version-specific normalization for the exact live BingX account response, first-run importer verification.
- Broker positionId-based matching and funding attribution, accounting for partial liquidation / ADL and concurrent positions.
- Append-only signal event queue with durable retry and automatic audit of dropped Combo tags.
- Date-filter-consistent account PnL metrics (the main fills figure intentionally shows all stored fill history for filtered symbols).
- Per-Combo equity normalized by allocated margin, net exposure and risk statistics.

## Source documentation

- BingX-API [`swap-trade/api-reference.md`](https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-trade/api-reference.md)
- BingX-API [`swap-account/api-reference.md`](https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-account/api-reference.md)
