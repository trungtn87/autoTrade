# AutoTrade V3-03 — Local M1 execution *scenario* simulator

Status: C1 engine implemented, synthetic test suite and real M1 dry run; **C2 (BingX-calibrated) NOT STARTED**.

## Scope and safety

Runs on PC with V3-02 `signal_events.csv` and the existing V3-01 DuckDB `v3_m1` catalog. Does not import the V2 executor, sign private HTTP calls, place orders, alter Supabase, or modify strategy 20FINAL. The only V2 file it loads is the **pure locked config** `final14_config.py`, by read-only path, to retrieve `tp_pct/sl_pct` per combo. `signal_events.csv` contains entry candidates, not executed orders.

The simulated live-equivalent **default exit profile is an explicit assumption**, not proven historical exit_mode: 50% fixed TP + 50% exchange-native trailing, full SL, activation 0.5% and callback 0.5%. A second profile `hard_tp` closes 100% at fixed TP/SL. The live runtime can vary by deployment and minimum quantity fallback; V2 export in V3-04 will distinguish real modes. The V2 executor uses 100 USDT target notional, 50x leverage and rounds quantity with exchange rules: this simulator approximates quantity by 4 decimal digits (actual contract precision and minimum notional may differ).

## Order path assumptions

- Enter no earlier than the first complete M1 **after** the signal has closed, plus `entry_latency_ms`, using that next eligible candle's **open**. Sub-minute fills are deliberately *not* invented. Coarse next-minute rounding becomes a conservative time delay, not a latency prediction.
- Both fixed M1 price trajectories are evaluated independently: `OPEN→HIGH→LOW→CLOSE` and `OPEN→LOW→HIGH→CLOSE`. This captures basic TP/SL both-touched and trailing-path ambiguity. **Their min/max net PnL are scenario samples, NOT rigorous mathematical bounds** of every possible tick path or BingX native order chronology. Trading at price-basis **LAST** cannot reconstruct mark-price triggers.
- No exit fill is assumed during the **entry minute** (protection placement/ACK is unknown). If that minute touches stop, TP or trailing activation, the trade is marked with an explicit unobservable-sequence flag.
- On later minutes, a gap beyond SL triggers at the opening market price, not at an impossible pre-gap SL level. Each market fill has configurable unfavorable spread/slippage and per-leg fees.
- Split TP and trailing legs have separately tracked quantity and trigger state, protected by full SL; trailing activation/extreme updates along each deterministic price path. When both halves exit, there are no lingering virtual exits. This is a *research abstraction* and does not emulate live cancel/ACK races, coalesced native exit order quantities or BingX protection safety.
- One virtual position per combo is allowed. A new signal for the same combo is skipped while the longest plausible scenario stays open. **Different combos of the same symbol and direction are not blocked**, but overlapping periods are tagged as `shared_container_overlap`; no fake combo-level PnL allocation is claimed.
- Fees, spread, slippage and optional funding (a **fixed user-supplied rate** at 8h UTC slots, *not* real historical BingX funding) affect estimated net results. Approximate 50x liquidation wick is flagged because mark/margin maintenance history is not available. Partial exchange fills, real mark trigger, orderbook, network faults, recovery and exchange position merge are **NOT MODELED**.
- Trades still open at data end or max-holding censoring are not forced closed. PnL/ROI are `null` for open outcomes; unrealized value is reported separately. No look-ahead or interpolation across missing M1.

## Running on Windows local

From repo root, Python 3.12 (uses only stdlib + DuckDB and V2's pinned strategy config):

```powershell
python -m pip install -r autotrade_v3/requirements-replay.txt
python -m autotrade_v3.data_backtest.simulate_execution `
  --events "D:\AutoTradeV3\runs\V3-02\signal_events.csv" `
  --catalog "D:\AutoTradeV3\catalog\research.duckdb" `
  --out "D:\AutoTradeV3\runs\V3-03\split" `
  --exit-mode split_trailing --fee-bps 5 --entry-slippage-bps 2 `
  --exit-slippage-bps 2 --half-spread-bps 1

python -m autotrade_v3.data_backtest.simulate_execution `
  --events "D:\AutoTradeV3\runs\V3-02\signal_events.csv" `
  --catalog "D:\AutoTradeV3\catalog\research.duckdb" `
  --out "D:\AutoTradeV3\runs\V3-03\hard" --exit-mode hard_tp
```

Outputs: `execution_report.json` (SHA-256 code/config/profile/event, entry rejection/acceptance, ambiguity, overlaps, aggregate **scenario** amounts), and `scenario_trades.jsonl` (each trade, two deterministic path outcomes, per-leg fills, fees/funding, assumptions). No trade API creds, no remote DB or HTTP.

**Reproducibility:** pinned data/checksums from Gate A, frozen V3-02 signals from Gate B; `source_git_sha`, `simulator_sha256`, `config_sha256`, `profile_sha256`, `source_events_sha256`. Do not combine multiple profiles into one purported portfolio PnL.

## Direct three-year research run (2026-11-10)

The first run used the already-verified BTC/ETH M1 archive 2023-11-01..2026-11-01, V3-02 event CSV (6,752 raw; 6,199 accepted; 5,984 after warmup), 100 USDT notional/entry, 50x, 5bps fee/side, 2bps adverse slippage/side, 1bps half spread, 0 assumed funding. The historical 50% split exit_mode is **not** confirmed for individual signals.

| Research-only measurement | Split TP/trailing | 100% hard TP/SL |
|---|---:|---:|
| Virtual entries after same-combo occupancy | 4,147 | 4,155 |
| Signals skipped while same combo virtual position open | 1,837 | 1,829 |
| Path-divergent trades | 454 | 0 |
| Ambiguous including entry-minute/liquidation uncertainty | 555 | 121 |
| Trades overlapping other same-side combo (virtual risk) | 2,574 | 2,569 |
| Open/unresolved at 2026-11-01 end | 0 | 0 |

**These results are NOT exchange profits or live win rate.** Pure scenario sums and per-trade estimated ROIs remain in the JSON. They are not validated against historical BingX entry/exit fills, cannot be added safely across exchange-merged combos, and cannot support investment decisions. The counts are a useful sample size/complexity estimate for Gate C2 and Gemini research feasibility. The fixed-funding model of `0` means funding was not reconstructed, not that historical funding was absent.

## C1 acceptance & next steps

C1 test suite covers long/short, both same-minute TP+SL, trailing activation and reversal, 50/50 split vs hard mode, gap below stop, fee/slippage, entry latency and warmup, trailing after TP, candidate overlap, missing M1, open-end censor, funding signs, liquidation wick and offline-only report. A PASS means **scenario code behaves as specified on tests**, not that the BingX platform executes these exact fills.

**V3-04 / Gate C2:** import the SELECT-only `processed_targets`, `signal_links`, orders/fills/income, reconcile exact order and leg identity, calibrate slippage/latency distributions on historical trades, split chronologically into calibration and untouched holdout, and measure `EXCHANGE_VERIFIED` versus `SIMULATED/AMBIGUOUS/UNRESOLVED`. No private BingX key by default; V2 production remains the sole trade writer.
