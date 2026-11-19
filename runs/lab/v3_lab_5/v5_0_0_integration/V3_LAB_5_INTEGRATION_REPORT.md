# V3_LAB_5@5.0.0 Integration & Verification Report

**Verdict:** `READY_FOR_CLAUDE`  
**Scope:** Integrate pure candle-only `ETH-USDT/C15` (`combo_id=105`) variant `V5_SELL_ONLY_ATR140` onto `V3_LAB_4@4.1.1` (keeping 29/30 combos strictly identical).  
**Mode:** `RESEARCH_AND_L2_HANDOFF_ONLY` (no `autotrade_v3/live`, `20FINAL`, `runs/golden`, `strategy_core`, `autotrade_v2`, or `V3_LAB_4*` modified).

## 1. Lineage, Hashes & Environment

| Field | Value |
| :--- | :--- |
| Repository | `trungtn87/autoTrade` |
| Base Branch | `v3_lab` |
| Head Branch | `feat/v3-lab-5-eth-c15-v5` |
| Baseline Commit SHA | `d11e37d42dd04a3db82e55bc8df408c1450bd143` |
| L2 Source Commit SHA | `11b61a2c550e15079e4c299839c7512fa52829eb` |
| Draft PR URL | `https://github.com/trungtn87/autoTrade/pull/147` |
| Strategy ID / Version | `V3_LAB_5@5.0.0` |
| Dataset Hash (M1 3Y `2023-11-01`..`<2026-11-01`) | `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628` |
| Config Hash (`v3_lab_5_config.json`) | `a9ac70337477f2d338753fc80158382e013f1ba263c468559444d5be93db33bc` |
| Signal Spec Hash | `1b8d6d0f856abcaedecb1d39de1e74022a5cf8713e9743d9e8562c1d1158ebd9` |
| Exit Spec Hash | `11617415ec7aa0cbde95a1b3c67a6254c416cc971aee573858f86da949b37e11` |
| Combined Strategy Spec Hash | `631cd361bc0a1100675e4c77c783ae64952854c6f286694bf71306329227372e` |
| C15 V5 Filter Config Hash | `e831925538c23cdae37511ce945ab409b97922c95e477abd58b0f3df686a2fb3` |
| Simulator | `UnifiedSimulator` (`CONSERVATIVE`, initial_equity=1000.0, notional=100.0) |

## 2. Gate Summary

| Gate | Status | Summary |
| :--- | :--- | :--- |
| **A. 29-Combo Parity** | `PASS` | 29/29 non-C15 combos 0 raw diffs and 0 approved diffs vs V3_LAB_4@4.1.1 (6789 approved signals matched) |
| **B. ETH-C15 V5 OTPC Parity** | `PASS` | 1646 OB inputs -> 480 kept / 1166 dropped; IDEAL/BASE/STRESS match Round 2 trade-by-trade (`186 / +$48.23`, `187 / +$43.60`, `191 / +$20.19`) |
| **C. 30-Combo Portfolio & Own/VWAP** | `PASS` | Exact parity vs Round 2 V5 ledgers (`BASE +$320.19`, `STRESS -$4.66`); Own/VWAP diff `0.0` |
| **D. L2 Audit & Zero-Lookahead** | `PASS` | Candle-only AST, real 3Y truncation no-lookahead, deterministic replay, subprocess simulator independence, zero stateful `H3AsymmetricController` |

## 3. Gate A — 29 Non-C15 Combos Exact Parity

- Full CSV: [V3_LAB_5_PARITY_29.csv](V3_LAB_5_PARITY_29.csv)
- `only_approved=False` (raw): 7447 raw signals matched (0 diffs)
- `only_approved=True` (approved): 6789 approved signals matched (0 diffs)
- Notable pure-package switch: `ETH-USDT/C1` migrated internally from `scripts/p3_c1_research_strategy.py` to `autotrade_v3/strategy/eth_c1_h1_both_1.py` with **0** signal differences and identical `exit_policy_hash` across all 3 years.

## 4. Gate B — ETH-USDT/C15 V5_SELL_ONLY_ATR140 Standalone Parity (`ONE_TRADE_PER_COMBO`)

- Filter decision breakdown on 1646 1H OB-approved C15 inputs (3Y):
  - Kept (`SELL`, `atr_ratio_50 <= 1.40`): **480** signals
  - Blocked `BLOCKED_V5_BUY_DISABLED`: **881** signals
  - Blocked `BLOCKED_V5_SELL_ATR_RATIO_GT_140`: **285** signals

| Profile | V5 Trades (B/S) | R2 Ref Trades | V5 Own Net | R2 Own Net | Diff | Fee Total | Slippage | EV (Own) | PF | Standalone M2M DD | Status |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| IDEAL | 186 (0B/186S) | 186 | +48.23 | +48.23 | +0.000000 | 18.57 | 0.00 | +0.2593 | 1.4915 | $13.87 | PASS |
| BASE | 187 (0B/187S) | 187 | +43.60 | +43.60 | +0.000000 | 18.67 | 7.47 | +0.2332 | 1.4306 | $14.69 | PASS |
| STRESS | 191 (0B/191S) | 191 | +20.19 | +20.19 | +0.000000 | 19.08 | 19.08 | +0.1057 | 1.1785 | $18.14 | PASS |

### ETH-C15 V5 Yearly Breakdown (`ONE_TRADE_PER_COMBO`)

| Profile | Year | Trades | Win Rate | Own Net PnL | EV / Trade | PF |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| IDEAL | 2024 | 49 | 42.86% | +14.71 | +0.3002 | 1.5835 |
| IDEAL | 2025 | 49 | 42.86% | +14.71 | +0.3002 | 1.5835 |
| IDEAL | 2026 | 88 | 39.77% | +18.81 | +0.2138 | 1.3942 |
| BASE | 2024 | 50 | 42.00% | +12.81 | +0.2563 | 1.4799 |
| BASE | 2025 | 49 | 42.86% | +13.73 | +0.2803 | 1.5328 |
| BASE | 2026 | 88 | 39.77% | +17.06 | +0.1938 | 1.3496 |
| STRESS | 2024 | 48 | 37.50% | +4.81 | +0.1002 | 1.1687 |
| STRESS | 2025 | 53 | 37.74% | +5.66 | +0.1068 | 1.1805 |
| STRESS | 2026 | 90 | 37.78% | +9.72 | +0.1080 | 1.1826 |

## 5. Gate C — 30-Combo Portfolio (`BINGX_PORTFOLIO`) & Own/VWAP Conservation

| Profile | V5 Trades | H3 Trades | V5 Net | R2 V5 Net | H3 Net | Delta V5-H3 | V5 M2M DD | H3 M2M DD | Own/VWAP Diff | Other-29 Delta | Status |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| IDEAL | 4839 | 5070 | +528.16 | +528.16 | +544.25 | -16.09 | $68.59 | $68.39 | 2.27e-13 | -7.62 | PASS |
| BASE | 4853 | 5082 | +320.19 | +320.19 | +327.89 | -7.71 | $72.87 | $72.71 | 2.25e-11 | -6.91 | PASS |
| STRESS | 4873 | 5104 | -4.66 | -4.66 | -19.73 | +15.07 | $89.19 | $96.49 | 5.32e-11 | -2.50 | PASS |

- Full CSV: [V3_LAB_5_PORTFOLIO_COMPARISON.csv](V3_LAB_5_PORTFOLIO_COMPARISON.csv)
- Replacing stateful `H3_ASYMMETRIC` with pure candle-only `V5_SELL_ONLY_ATR140` improves 30-combo portfolio `STRESS` Net from `-$19.73` to `-$4.66` (`+$15.07`), reduces `STRESS` M2M DD from `$96.49` to `$89.19`, and reduces C15 3Y trades from `416` to `187` (BASE) with **0** signal changes on the other 29 combos (Other-29 VWAP deltas of `-$7.62` IDEAL / `-$6.91` BASE / `-$2.50` STRESS reflect concurrent ETH-USDT VWAP pooling shifts while `Own == VWAP == Net` conservation holds within `5.32e-11`).

## 6. Gate D — Candle-Only & Zero-Lookahead Audit

- `1_candle_only_ast_audit`: **PASS** — 0 forbidden imports across filter/strategy/loader (0 violations)
- `2_zero_stateful_h3_controller`: **PASS** — 0 stateful H3AsymmetricController / build_c15_h3_controller code paths (0 refs)
- `3_zero_lookahead_real_3y`: **PASS** — 4698 signals before 2025-11-01T00:00Z matched 100% after post-cutoff OHLCV perturbation
- `4_warmup_and_closed_1h_bars`: **PASS** — warmup_bars=3400, all signals closed-bar aligned, min_signal_close_ms=1701856799999
- `5_deterministic_replay`: **PASS** — Full 3Y replay sha256=a630e6f622145a584fcccad3b40c07abe4955438080fa2388d84c9776a49cfd0 matched across repeat invocations (n=7269)
- `6_simulator_independence_subprocess`: **PASS** — Subprocess L2 execution with sim engines & scripts.run_v3_lab_2_eth_c15_h3_asymmetric blocked exited 0
- `7_hash_verification`: **PASS** — config_hash=a9ac70337477f2d338753fc80158382e013f1ba263c468559444d5be93db33bc, spec_hash=631cd361bc0a1100675e4c77c783ae64952854c6f286694bf71306329227372e

## 7. Non-OOS Disclosure & Shadow Requirement

> [!WARNING]
> The historical 3-year window (`2023-11-01` to `<2026-11-01`, including EVAL `2025-11-01`..`<2026-11-01`) was previously observed during prior H0–H3 and Round 2 research. EVAL is **not** an independent out-of-sample guarantee; forward/shadow validation on post-`2026-11-01` market data is mandatory before scaling live allocation.
