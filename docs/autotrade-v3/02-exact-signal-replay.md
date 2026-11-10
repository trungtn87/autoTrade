# V3-02 — Replay locked 20FINAL signals (local first)

Reuses the existing pure FINAL14 + NEW6 20FINAL strategy from V2; **does not import trading executor, BingX private API or live DB**. This is a batch runner for historical raw/approved signals, **not** TP/SL or win-rate backtest.

Requirements: Python 3.12 and exact pinned pandas/numpy/DuckDB versions in autotrade_v3/requirements-replay.txt.

Run after V3-01 has indexed the already verified Library M1 archive:

    python -m pip install -r autotrade_v3/requirements-replay.txt
    python -m autotrade_v3.data_backtest.replay_20final --catalog "D:/AutoTradeV3/catalog/research.duckdb" --out-dir "D:/AutoTradeV3/runs/V3-02"

Outputs: signal_events.csv (candidate+approved, time, native 1H/M15, SMC/OB status, provider at signal close, warmup flag); replay_report.json (monthly approved signals, raw/passed counts and SHA256, exact code hashes/Git SHA).

**B1 exact same-source parity:** frozen reference in data_backtest/run17_reference.json copied from a successful 2026-11-10 GitHub FINAL14 Full-History Parity workflow (14 FINAL14 + six NEW6). The existing independent parity check is PASS for old research vs current V2 code on identical RUN17 M15 inputs. The V3 replay itself has now been run on safe-converted RUN17 M15: **20/20 exact combo-signature parity PASS**, as documented in 02-b1-b2-verified.md. M1-derived parity remains a separate B2 source sensitivity measurement.

**B2 source sensitivity:** RUN17 hybrid M15 ends 2026-09-20, while archived M1 hybrid starts 2023-11-01. Binance M1-derived M15 is a different price series; expected output mismatch is an empirical sensitivity finding, not a strategy bug. First 3,400 M15 bars are flagged WARMUP_ONLY rather than represented as live-ready. 1H signals emit only after native 1H closes, never before.

Approved signals count is a **sample-size upper bound** for strategy research, not independent closed trades; execution conflicts, same-symbol position merging and fees are only handled in V3-03/04.

No changes to production 20FINAL or server, no data download, no Supabase schema write.


**2026-11-10 verified results:** full 3-year V3 real-M1 replay + RUN17 20/20 same-input B1 + source-sensitivity B2 are documented in 02-b1-b2-verified.md.
