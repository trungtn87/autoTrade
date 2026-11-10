# AutoTrade V3-04B — BingX Native Exit Evidence (local first)

**Implementation:** standalone Python-stdlib CLI, no credential handling, HTTP requests, database clients, app service or trading action. V2 20FINAL, the existing V3-04 export and Supabase production are **unchanged**. **Gate C2 remains `INCONCLUSIVE`.**

## 1. Why the extra evidence is necessary

Read-only Supabase inspection on **2026-11-10**:

| Evidence | Observed |
|---|---:|
| `autotrade_analytics.signal_links` | 132 signals/entries |
| Entries with detailed exact fills and matching quantity | 59 |
| Linked signals with explicit `exit_mode` | 37 |
| Linked SL/TP/trailing protection ID | 130 / 130 / 32 |
| Linked SL/TP/trailing matched directly to fills in V2 | 0 |
| `bingx_orders` / `bingx_fills` / `bingx_income` | 435 / 134 / 497 |
| Income entries matched to fill `trade_id` | 0 |
| V2 orders with `workingType=MARK_PRICE` | 432/435 |

In the whole order set, **99 STOP_MARKET, 28 TAKE_PROFIT_MARKET and 16 TRAILING_STOP_MARKET** rows have `triggerOrderId` different from their own order ID. This is a **candidate** link, *not by itself proof of a triggered execution fill*. Order details and actual exchange fill records must agree **by exact order ID**; neither adjacent timestamps nor common symbol/price are acceptable substitutes.

Existing M1 2023-11-01–2026-10-31 UTC is predominantly **Binance last price**, not BingX MARK; only the 2026-10-28 supplement is BingX last price. Mark-price protection and native trailing execution **cannot** be reproduced exactly by the local last-price M1 simulator.

## 2. Data inputs / source provenance

First export V2 data once as **SELECT-only**, using `scripts/v3-04-export-readonly.psql` and the V3-04 `seal` command. Never put PostgreSQL tokens or BingX private keys in V3.

To obtain **new exchange evidence**, use a *separate BingX read-only account/API key*, with **trading and withdrawals disabled** and IP whitelist, **outside** the V3 backtest. Save the user-authorized GET responses into files on your own PC. No such key is connected in this PR, so the PR does not fetch private exchange data or claim to have done so.

Official BingX perpetual swap documentation lists read-only endpoints including:

- `GET /openApi/swap/v2/trade/allOrders` — order history
- `GET /openApi/swap/v2/trade/order` — query a single order by exact ID
- `GET /openApi/swap/v2/trade/fillHistory` and `GET /openApi/swap/v2/trade/allFillOrders` — execution/fill history
- `GET /openApi/swap/v1/trade/positionHistory` — closed position history

Reference: https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-trade/api-reference.md

**Keep pagination and date-window coverage metadata separately.** A file without errors/checksum may still be incomplete; `seal` intentionally writes `complete_history_attested=false`. Never turn an empty page into evidence that an old order/fill never existed. Keep raw authenticated API responses private; do **not** commit them or the normalized rows to GitHub.

## 3. Normalize saved GET response JSON without network access

Python 3.12, no external packages:

```powershell
python -m autotrade_v3.data_backtest.normalize_bingx_history `
  --orders-json "D:/AutoTradeV3/exports/bingx/orders.json" `
  --fills-json "D:/AutoTradeV3/exports/bingx/fills.json" `
  --out-dir "D:/AutoTradeV3/exports/bingx/normalized"
```

Each input is one response (`{"code":0,"data":{"orders":[...]}}` / `{"code":0,"data":{"fill_orders":[...]}}`) or an array of previously saved paginated responses. The normalizer retains **only** an allowlist of order/fill fields. It rejects provider errors, unknown layouts, malformed/missing IDs and duplicate identities; large IDs are stored **as exact decimal strings** and never parsed as JavaScript floats.

`orders.csv` fields: `symbol,order_id,side,position_side,type,status,position_id,filled_qty,trigger_order_id,main_order_id,working_type`.

`fills.csv` fields: `symbol,trade_id,order_id,side,position_side,qty,price,fee,realized_pnl,timestamp_ms`.

If a provider response lacks `positionSide`, actual quantity, timestamps or finance data, the corresponding fill/order remains **UNRESOLVED** until supported by additional evidence. No defaulting side/position to a guessed value.

### Optional explicit parent–child proof

`triggerOrderId` in `orders.csv` is only a **candidate**, not a trusted ownership link. For an explicit relation, include `explicit_links.csv` with columns:

`symbol,parent_order_id,child_order_id,proof_source,proof_sha256,proof_path`

- `proof_source` must equal `BINGX_ORDER_DETAIL`.
- `proof_path` must be `proofs/<filename>.json` inside the normalized export folder.
- The original saved, successful order-detail JSON must contain `data.orderID` (or `orderId`) equal to **parent_order_id** and `data.triggerOrderId` equal to **child_order_id**.
- `proof_sha256` must match the actual bytes of that JSON, as must the sealed manifest's proof-file checksum.
- An exported triggered child order must exist with the same symbol, inverse side, correct positionSide and exact positionID, and its own exchange fill ID and filled quantity must reconcile.

**No proof document = no promotion to `EXIT_FILL_SUPPORTED`.** A wrong parent, missing child, mismatched position ID, duplicate trade ID or reused hedge container keeps the row unresolved. If BingX doesn't expose an authoritative link through these fields for a historical order, leave it unresolved; do not invent one.

### Seal and run

```powershell
python -m autotrade_v3.data_backtest.verify_bingx_exits seal `
  --bingx-dir "D:/AutoTradeV3/exports/bingx/normalized" `
  --exported-at-utc 2026-11-10T04:00:00Z

python -m autotrade_v3.data_backtest.verify_bingx_exits audit `
  --v2-dir "D:/AutoTradeV3/exports/v2/<date>" `
  --bingx-dir "D:/AutoTradeV3/exports/bingx/normalized" `
  --out-dir "D:/AutoTradeV3/reports/C2B"
```

The timestamp is an **example**; use the true export instant in UTC. The V2 input must already have `manifest-v3-04.json` produced by V3-04. You can omit `--bingx-dir` for a **negative-evidence inventory** (it then refuses to claim matching exchange fills).

Outputs: `gate_c2b_report.json` (summary by month, status and counts; SHA-256 references); `exit_lineage.jsonl` (hashed signal identity, reasons, supported exit fill quantities, **null verified net PnL/ROI**). Neither report contains exchange order IDs or account secrets. Private original exports stay entirely local.

The separate `scripts/v3-04b-order-candidates-readonly.psql` produces *candidate* protective order IDs, original trigger fields and working type from current V2 tables for deciding which **GET** details are needed next. Those candidate fields **do not** count as independent exchange evidence.

## 4. Strict Gate C2 states

| State | Interpretation |
|---|---|
| `ENTRY_EVIDENCE_UNRESOLVED` | No exact Entry order and matching real fills, or identity/quantity disagreement |
| `EXIT_LINEAGE_UNRESOLVED` | No trustworthy parent→trigger child evidence, order identity or fill |
| `EXIT_FILL_INCOMPLETE` | Exact Exit fill exists but does not prove full closure |
| `MERGED_POSITION_UNRESOLVED` | Same hedge position or protective child shared; per-combo allocation unsafe |
| `EXIT_FILL_SUPPORTED_FINANCE_UNRESOLVED` | Exact filled Exit and quantity can be supported, **not net income / ROI** |

Regardless of state, C2 is **INCONCLUSIVE** until verified independent funding allocation, complete historical export coverage, and sufficient paired holdout observations. There is no automatic `EXCHANGE_VERIFIED_NET` promotion in this PR. **A green CI is not a real exchange evidence PASS**; tests use synthetic fixtures only.

## 5. Tests, non-goals, and next decision

Run locally: `python -m unittest autotrade_v3.tests.test_verify_bingx_exits autotrade_v3.tests.test_normalize_bingx_history -v`. CI runs the same on Python 3.12 with no exchange credentials.

No POST/DELETE/PUT HTTP, no live BingX order API, no Supabase write/migration, no V2 daemon change, no market data download, and no future-looking nearest-time PnL assignment.

If historical BingX GET export cannot supply exact triggered child/position/funding ownership, mark **C2 INCONCLUSIVE** and continue only with scenario-based research explicitly labeled **SIMULATED**, not a verified trade analysis or AI auto-gate.