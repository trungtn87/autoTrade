# Autotrade Live Dashboard (BingX USDT Perpetual)

A standalone **read-only** analytics package for **real BingX futures executions**. Existing Autotrade trading and TradingView backtest workflows are not modified.

## Đối chiếu kết quả TP / SL / trailing theo Combo

The dashboard independently reads the bot's *already-persisted* V2
`public.processed_targets` order IDs, TP/SL targets and signal times.
It cross-references those records with the existing daily BingX read-only
archive. No new REST calls are added to the live trading service.

- `ORDER_ID`: exact recorded TP, SL or trailing order ID matched to an
  executed BingX fill. This is a confirmed classification.
- `PRICE_TIME_CANDIDATE`: order ID missing, but unique symbol, side,
  position, trigger/activation price, quantity and time are compatible.
  This is a candidate, **not proof**, and is excluded from trusted sums.
- `AMBIGUOUS` / `CONFLICT` / `UNMAPPED`: no safe automatic Combo mapping.
  Never silently assign a shared/multi-Combo position to one strategy.
- `bingx_realized_pnl` is the exchange's value on the **exit fills**,
  not a reconstruction of each Combo's independent strategy PnL.
  BingX can use a blended entry for simultaneous Combo positions.
  Exit fees are separate; entry fees/funding are not allocated.
- The new exit section supplements, but does not modify, the old
  position-level PnL statistics.
- A canceled unfilled SL is **not** a realized exit. Only BingX execution
  fills appear in the exit history.

The existing GitHub scheduled collector remains unchanged; the dashboard
will not show new fills until that collector synchronizes them. If the
dashboard cannot SELECT `public.processed_targets`, exit matching degrades
to `UNMAPPED` and the rest of the dashboard continues read-only.

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

Use two components sharing the same Supabase private analytics schema:

1. **Daily collector (free GitHub Actions on public repository)**: `.github/workflows/daily-bingx-analytics.yml` checks `BINGX_READ_API_KEY`, `BINGX_READ_API_SECRET` and `DATABASE_URL` in repository Actions secrets. It runs `python -m autotrade_live.collect --days 3` once daily at 10:17 Vietnam time, with overlapping idempotent upserts. Manual `workflow_dispatch` allows a first backfill of 30/60/90 days (within BingX retention). See [DAILY-SYNC.md](DAILY-SYNC.md).
2. **Read-only mobile dashboard (Render Free web service)**: `uvicorn autotrade_live.mobile:app --host 0.0.0.0 --port $PORT` from `dashboard/`, with `DATABASE_URL`, `DASHBOARD_PASSWORD`, `APP_SESSION_SECRET`, `DB_SCHEMA=autotrade_analytics`. The PWA at `/mobile` is password-protected and cannot trade. The separate optional Combo receiver is included at `/collector/v1/signals` but is not required: the daily collector reads Combo labels directly from `public.processed_targets`.

The existing Autotrade bot is unchanged. This setup uses **no Render paid cron job** and stores no credentials in Git. Historical data is only available once a successful real BingX sync has completed and been reconciled.

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
