# baseline_20final.py: checksum change and regression evidence (2026-11-19)

- Old sha256 (main): c5da0d32f268f9e5b03d381f0f4a987af2a97637c19c2a3958409f097612af4c
- New sha256 (PR #145 a18f2ad): 1102811ab193785bda1279deb18b91297a9cc4a06a76093ff39cefc644dbf8bf

Regression method: baseline_20final_regression.py run on synthetic, seeded M15 data
(BTC-USDT/ETH-USDT, 20000 bars, seed 20261119) through Baseline20FinalStrategy.generate_signals
(only_approved=False, only_warmup_ready=False). Old = origin/main full stack, new = integration branch.

Result: old 602 signals, new 602 signals, identical on every field (symbol, combo, side, close ms,
entry, tp/sl pct, tp/sl price, approved). See baseline_old_signals.json and baseline_new_signals.json.

Limits: synthetic data only (no DuckDB or BingX history on cloud). Real-data parity is the lab's
responsibility (Antigravity reports 5,780/5,780 signal parity on local data). No V2 file touched.
