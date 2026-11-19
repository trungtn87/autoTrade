# LIVE vs BACKTEST PARITY REPORT — AUTOTRADE V3 `FIXED_TP_SL`

**Overall Parity Status:** `PARITY_PENDING_LIVE_VERIFICATION`  
**Backtest Strategy Baseline:** `20FINAL_FIXED_V1@1`  
**Simulator Version:** `v3.16-fixed-tpsl`  
**Live Reference Inspected:** `origin/main` (`30367d7`) & worktree `feat/v3-fixed-tp-sl-discord` (`C:/tmp/autotrade-v3-fixed-tpsl`)

> [!IMPORTANT]
> Codex's `FIXED_TP_SL` Live PR on `feat/v3-fixed-tp-sl-discord` is not yet committed/merged (`C:/tmp/autotrade-v3-fixed-tpsl` is currently at `origin/main` `30367d7` with clean working tree). Per design principles, no Live code (`autotrade_v2`, `unified_app.py`, VPS, BingX API, Discord) was modified. The Local Backtest simulator implements the expected `FIXED_TP_SL` contract and is marked `PARITY_PENDING_LIVE_VERIFICATION`.

---

## 1. Parity Classification Matrix

| Rule / Component | Status | Live Reference (`autotrade_v2`) | Local Backtest (`autotrade_v3`) | Gap / Notes |
|---|---|---|---|---|
| 20 Combos, Indicators, Entry Conditions, Timeframes, BUY/SELL | `MATCHED` | `autotrade_v2/app/strategy/final14_config.py`, `final14_exact_strategy.py` | `20FINAL_FIXED_V1@1` (`signal_spec_hash = 4729dfeabea57994d1e955c9f8ce4716b4fbe118cbce2765fa8fddeeaf17da79`) | `signal_spec_hash` is bit-identical to `20FINAL@1`. |
| TP% / SL% per Combo | `MATCHED` | `final14_config.py` | `20final_fixed_v1_v1/params.json` | All 20 `tp_pct` and `sl_pct` values match `20FINAL@1`. |
| 100% Fixed TP/SL Structure (No Trailing, No Breakeven) | `UNVERIFIED` | `final14_executor.py` on `origin/main` still contains `split_trailing` / `allocate_exit_qty` | `split_legs=False`, `leg_weights=(1.0,)`, `trailing_act_pct=0.0`, `trailing_cb_pct=0.0` | Pending Codex's `FIXED_TP_SL` PR on `feat/v3-fixed-tp-sl-discord`. |
| TP/SL Price Formula from Filled Entry | `MATCHED` | `avgPrice * (1 ± pct)` | `entry_px_fill * (1 ± tp_pct / sl_pct)` in `sim/leg.py` | Uses simulated filled entry price (including slippage), never raw signal price. |
| BingX Price & Quantity Rounding | `PARTIAL` | `final14_executor.py:380-385` (`pricePrecision`, `ROUND_HALF_UP`), `quantity.py` (`quantityPrecision`, `tradeMinQuantity`, `tradeMinUSDT`) | `autotrade_v3/sim/precision.py` implements exact `Decimal` helpers; default 3-yr backtest runs continuous float qty/price | Offline backtest does not call BingX API for live contract rules; continuous mode is documented in `MODE_C_SIMULATION_LIMITS`. |
| BingX Portfolio Position Merging & VWAP (`BINGX_PORTFOLIO`) | `PARTIAL` | `merged.py` (`reconcile_side`, `EXECUTABLE_EXIT_BUDGET_EXCEEDED`) | `AccountLedger` merges `(symbol, side)` VWAP, checks free margin, tracks per-combo `leg_qty` & `pnl_vwap` | Backtest models merged VWAP & per-combo exit isolation; does not simulate BingX `positionId` container restarts in market runs. |
| Multi-Combo Coexistence & Exit Isolation | `PARTIAL` | `merged.py:119-142` | `AdmissionPolicy` (`Portfolio`) gates per `(symbol, combo_id)` and closes only own `leg_qty` | Covered in isolated scenario tests (`test_fixed_tpsl_execution_scenarios.py`). |
| Intrabar Tick Ordering & Open Gap | `PARTIAL` | Live exchange matching engine (tick-level) | M1 OHLC `CONSERVATIVE` ambiguity policy + open gap check (`sim/ambiguity.py`, `sim/leg.py`) | M1 OHLC bars lack intrabar tick sequence; `CONSERVATIVE` resolves simultaneous TP/SL hits to SL. |
| Orderbook Slippage, Fees, Funding Rate | `PARTIAL` | Real BingX taker/maker fills + 8h funding | `CostModel` (`IDEAL` 0 bps, `BASE` 2 bps/side, `STRESS` 5 bps/side, taker fee 5 bps); funding `NOT_MODELED` | No synthetic funding/mark-price invented without valid source. |
| Partial Fills, API Timeout, SL/TP Reject, Force Close, Restart | `PARTIAL` | `final14_executor.py`, `protection_journal.py`, `force_close.py` | Tested separately in `test_fixed_tpsl_execution_scenarios.py` (11 scenarios) | `tp_reject` behavior in `FIXED_TP_SL` marked `UNVERIFIED` pending Codex PR. |

---

## 2. Verification Boundary

- **Not Claimed as `MATCHED`:** Profit similarity to prior A/B experiments (`+$516.36` / `+$338.01` / `+$57.25`) is NOT used as proof of Live parity.
- **Next Step for Full `MATCHED` Status:** Once Codex merges the `FIXED_TP_SL` PR on `feat/v3-fixed-tp-sl-discord`, re-verify L3 → L4 `TradeIntent` / `Final14Executor` TP/SL placement, `tp_reject` handling, and contract precision rules against this report.
