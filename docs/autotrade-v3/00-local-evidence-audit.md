# AutoTrade V3-00 — Local-only evidence inventory

Status: **implemented inventory CLI; Gate G0 stays INCONCLUSIVE until real exit identity is audited**.

This PR does not modify V2, does not connect to BingX or Supabase, and does not create database migrations. It implements the first stage of the V3 Rev 2 local-first plan. The tool deliberately cannot label any EXIT as verified or calculate a win rate/PnL.

## Inputs

Export the relevant tables from **autotrade_analytics** and **public** using a read-only Supabase SQL connection or an offline SELECT-only export. One CSV per table; required filenames and columns:

- signal_links.csv — entry_order_id, signal_id, symbol, side, combo, signal_time_ms
- bingx_orders.csv — order_id, symbol, side, position_side, type, filled_qty
- bingx_fills.csv — trade_id, order_id, symbol, side, position_side, qty

Optional, only if the tables exist:

- trade_results.csv — event_id, payload (JSON)
- v2_states.csv — event_id, payload (JSON, from a trusted V2 state export; not a table in the current Supabase schema)
- ai_reviews.csv — event_id, symbol
- processed_targets.csv — event_id, target, payload (JSON, from public.processed_targets; contains state snapshots including exit_mode)

CSV columns not listed here are ignored by the inventory and **never copied into reports**. Export should avoid all raw provider JSON, passwords, API keys, personal information and chat history. Do not run this using a Supabase service-role key embedded in an application or shell script. Query only SELECT allowlisted columns. A missing optional table is **not equivalent** to an existing, empty table.

Example SELECT-only psql exports after a suitable *read-only* role has been configured by the owner (do not run DDL or grant permissions as part of V3):

    \copy (SELECT entry_order_id,signal_id,symbol,side,combo,signal_time_ms FROM autotrade_analytics.signal_links) TO 'signal_links.csv' CSV HEADER
    \copy (SELECT order_id,symbol,side,position_side,type,filled_qty FROM autotrade_analytics.bingx_orders) TO 'bingx_orders.csv' CSV HEADER
    \copy (SELECT trade_id,order_id,symbol,side,position_side,qty FROM autotrade_analytics.bingx_fills) TO 'bingx_fills.csv' CSV HEADER
    \copy (SELECT event_id,target,payload FROM public.processed_targets) TO 'processed_targets.csv' CSV HEADER

Export these CSVs into one directory outside Git, e.g., D:/AutoTradeV3/exports/v2/2026-11-10/.

## CLI, no external Python packages

From repo root (Python 3.12):

    python -m autotrade_v3.audit seal --input-dir D:/AutoTradeV3/exports/v2/2026-11-10 --exported-at-utc 2026-11-10T00:00:00Z
    python -m autotrade_v3.audit audit --input-dir D:/AutoTradeV3/exports/v2/2026-11-10 --output D:/AutoTradeV3/reports/g0-audit.json
    python -m unittest discover -s autotrade_v3/tests -v

The timestamp must match when you actually exported the data (UTC). Sealing writes local manifest.json with CSV SHA-256 and row counts; auditing rejects modified data. Reports include **only aggregates** by symbol/combo/month and reasons, never IDs, raw JSON or exchange credentials. Source CSV contains financial history; keep it private and backed up, and never commit it.

## What this checks

- Signal-to-entry exact link by entry_order_id **and** symbol, plus order side/type/position_side;
- Multiple entry links for one signal are unresolved, never arbitrarily chosen;
- Exact entry fill quantity/side/position_side; no "filled" inference without a fill row;
- Which linked signal events lack an explicit saved exit_mode in optional V2 state/result payloads;
- Missing trade_results/state exports vs present empty tables; unmatched AI reviews;
- Status breakdown and all linked signals counted, no discarded difficult samples.

**Not yet established:** order parent → triggered child identities, stop/TP/trailing mode and mark/last trigger, actual exit quantity/fee/PnL allocation, merged positions, and live-vs-sim agreement. Those require the full read-only evidence contract in V3-04. G0 remains INCONCLUSIVE until audit of available exit evidence is complete. Neither entry fill nor "CLOSED" from a result payload constitutes a verified exit.

## Baseline inventory observed from production (read-only, 2026-11-10)

- Exact counts: 132 signal_links, 432 bingx_orders, 134 bingx_fills, 497 bingx_income, 10 ai_reviews.
- All 132 linked entries found a matching order with expected symbol/side/entry type.
- **59 of 132** linked entries have exact-order fill records; a separate read-only aggregate check found complete matching fill quantities/side for all 59 (BTC 35/68, ETH 24/64). This still does not prove exit ownership.
- Order raw JSON has **0 exit_mode fields**; trade_results and trade_result_receipts tables are not present. However **public.processed_targets** contains 263 rows/134 distinct event IDs; 73 rows have top-level exit_mode and **37 of 132** linked Entries have a processed-target payload with exit_mode. This is real evidence of mode presence, not proof of exit ownership or complete fill.
- 295 stop/TP/trailing order rows and 72 corresponding fill rows exist, but must NOT be attributed to combos by symbol/time alone.
- These aggregates are a *read-only point-in-time snapshot*, not a complete exported dataset, and are **not validation of exited trade outcomes**.

## Safety and next step

No auto migration, no private exchange key, no requests to trading endpoints. Code imports only Python stdlib and never V2 engine modules. Next: export processed_targets together with exact entry/fill/exit/state evidence, run local inventory, then probe public historical M1 availability (V3-00P). Keep Gate G0 status INCONCLUSIVE until the missing exit evidence is accounted for.
