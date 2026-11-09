# Gemini BingX post-signal evidence (read-only phase)

Gemini chat reads the same **database-only** result projection as the existing
Dashboard (`dashboard.autotrade_live.results.load_results`). Do not replace it
with Binance price candles, create independent outcome math, or query live
BingX API. The AI does not modify the 20FINAL executor, order lifecycle,
TP/SL/trailing, Force Close or Dashboard.

## Match rules

- `ai_reviews.event_id` -> exact `signal_links.signal_id` ->
  `signal_links.entry_order_id` -> Dashboard projection by **symbol + order_id**.
- No link means `NO_CONFIRMED_ENTRY_LINK`, **not** a proven unexecuted trade.
- If multiple entry IDs map to a signal, report `MULTIPLE_LINKED_ENTRIES_UNRESOLVED`
  without choosing one.
- `CLOSED` + `bingx_db_verified`: exit reconciled from order data;
  money may be missing.
- `CLOSED` + `bingx_db_roi_estimate`: exit reconciled, ROI/PnL **estimated**
  from exact archived fills and fees.
- `ESTIMATED_CLOSED` + `bingx_db_merged_allocation_estimate`: closed
  **estimate** from balanced merged-position allocation; never a confirmed
  per-Combo actual.
- `CLOSED_PENDING` and `UNRESOLVED` contain **no** post outcome/ROI/PnL.
- Historical manually imported data keep explicit `MANUAL_HISTORICAL_RECORD`
  provenance, not presented as real exchange-verified results.

## Phase boundary

This phase fixes the private AI page's **read path**: new signal rows include
`bingx_post`, and Gemini private chat receives it alongside the original
immutable pre-Entry review and a quality legend. It does not yet create
`ai_post_reviews` or auto-send post-EXIT Discord messages. The existing
observer's strictly ledger-based post writer remains disabled until an
approved verified `trade_results` lifecycle contract exists.

No migration or backfill required. After normal deployment, visit /ai and
ask Gemini about an entry with a confirmed BingX order ID. Verify missing
entry links remain unknown, and merged results say estimated.

## Validation

- `python -m unittest autotrade_v2/tests/test_ai_bingx_feedback.py`
  (or the project's normal test runner with `autotrade_v2` on PYTHONPATH)
- Full production baseline, Postgres integration and FINAL14 parity.
- Independently verify /ai remains authenticated, Dashboard unaffected, and
  no execution code imports this reader.
