# V3_LAB_5@5.0.0 — L2 Source Handoff for Live Integration (Claude)

> [!IMPORTANT]
> **Single Source of Truth:** `V3_LAB_5@5.0.0` exposes pure candle-only Layer 1 + Layer 2 signal generation inside `autotrade_v3/`. Claude can pull this code directly from GitHub without re-implementing indicator or filter formulas in `autotrade_v3/live/`.

## 1. Git Repository, Branch, Commit SHA & Draft PR

| Item | Value |
| :--- | :--- |
| GitHub Repository | `https://github.com/trungtn87/autoTrade` (`trungtn87/autoTrade`) |
| Target Base Branch | `v3_lab` |
| Source Branch | `feat/v3-lab-5-eth-c15-v5` |
| Baseline Commit SHA (`V3_LAB_4@4.1.1`) | `d11e37d42dd04a3db82e55bc8df408c1450bd143` |
| L2 Source Commit SHA | `11b61a2c550e15079e4c299839c7512fa52829eb` |
| Draft PR | `https://github.com/trungtn87/autoTrade/pull/147` |
| Integration Status | `READY_FOR_CLAUDE` |

## 2. Executable L2 Source Files (Full Package Code on GitHub)

| File | SHA-256 |
| :--- | :--- |
| `autotrade_v3/filters/c15_v5_sell_atr140.py` | `ca4cabf44ead8f77dfd9ac197b946eed06414e07a90f5812179fe036baac8de8` |
| `autotrade_v3/filters/__init__.py` | `a27810eb2838686aa738baa3951d83182546745acda927eb82de8e6fd740ac48` |
| `autotrade_v3/strategy/v3_lab_5.py` | `a0273aeb421ad00d8617d23e75cd01f278d3a1390f7c2d93e2bd50fce33c2da2` |
| `autotrade_v3/strategy/registry.py` | `603d2a85d29a817894a91217c39594255502c943d26feee01e9f6933322c9f98` |
| `autotrade_v3/strategy/__init__.py` | `2336897986b5972cb9f8f2fbcff74f8f4ecc3bb7585d3fa3d6ac7cdf24de8ff6` |
| `autotrade_v3/strategy/eth_c1_h1_both_1.py` | `1c9c3eab2443bf84d24eea8174ec795d22327227a5a6764989657d096036763a` |
| `autotrade_v3/filters/c14_h3a_kumo_band.py` | `d2b282a3eadea67e17b7146ab14b06eb1db9b478cfa9fe24c4bab7d1ac3d320b` |
| `autotrade_v3/lab/v3_lab_5_loader.py` | `a3b54af862144df459ddae60d52b16b90ce78b1f1253646c2596ce5ba472d28e` |
| `autotrade_v3/lab/__init__.py` | `aa0b1ed892a242f394fa1f1568a7b1d86f89077d91a00c035ac193445f87b4d2` |
| `autotrade_v3/lab/v3_lab_5_config.json` | `061979b52538594aaf16417d637861860c040ae338aa4dc2936c4f11f2369d63` |

Fixture & verification artifacts:
- Synthetic parity fixture: `autotrade_v3/tests/fixtures/v3_lab_5_l2_parity_fixture.json`
- Integration test suite: `autotrade_v3/tests/test_v3_lab_5_integration.py`
- 3Y verification script: `scripts/verify_v3_lab_5_integration.py`
- Config JSON: `autotrade_v3/lab/v3_lab_5_config.json` (`config_hash=a9ac70337477f2d338753fc80158382e013f1ba263c468559444d5be93db33bc`)

## 3. Loader & Signal Generation API

```python
from autotrade_v3.lab.v3_lab_5_loader import (
    V3_LAB_5_ACTIVE_VERSION,
    V3_LAB_5_STRATEGY_ID,
    generate_v3_lab_5_signals,
    load_v3_lab_5_config,
    load_v3_lab_5_strategy,
)

# 1) Load validated strategy & config
cfg = load_v3_lab_5_config()
strategy = load_v3_lab_5_strategy()  # V3Lab5Strategy@5.0.0 via StrategyRegistry

# 2) Generate all 30-combo Lab signals
signals_30 = strategy.generate_signals(
    data={"BTC-USDT": df_btc_m15, "ETH-USDT": df_eth_m15},
    only_approved=True,
    only_warmup_ready=True,
)

# 3) Or generate ONLY a governed Live subset (e.g., 20-combo Live list) without touching 30-combo Lab
live_signals = strategy.generate_signals_for_combos(
    data={"BTC-USDT": df_btc_m15, "ETH-USDT": df_eth_m15},
    allowed_combos=LIVE_ALLOWED_COMBOS,  # set[tuple[str, int]]
    only_approved=True,
    only_warmup_ready=True,
)
```

For targeted C15 V5 evaluation alone over existing OB-approved C15 signals:
```python
from autotrade_v3.filters.c15_v5_sell_atr140 import C15V5SellAtr140Filter

c15_filter = C15V5SellAtr140Filter.from_m15_dataframe(df_eth_m15)
decision = c15_filter.evaluate(signal)  # FilterDecision(action="KEEP"|"DROP", reason=...)
```

**Zero stateful controller guarantee:** `V3Lab5Strategy` has **no** `H3AsymmetricController`, **no** `build_c15_h3_controller`, **no** post-TP cooldown state, **no** `scripts.*` runtime imports, and **no** DB/order/position/L3/L4 dependency.

## 4. Input Schema Requirements

| Field | Specification |
| :--- | :--- |
| Mapping | `dict[str, pd.DataFrame]` keyed by `"BTC-USDT"`, `"ETH-USDT"` |
| Index | `pd.DatetimeIndex` in **UTC** representing closed M15 bar **open timestamps** (e.g. `00:00`, `00:15`, ...) |
| Required Columns | `open`, `high`, `low`, `close`, `volume` (`float64`); optional `provider` (`str`, defaults to `"UNKNOWN"`) |
| Bar Completeness | Strictly **closed** M15 bars only; internal 1H (`resample_ohlcv(..., "1h")`) and 4H (`resample_ohlcv(..., "4h")`) bars are evaluated strictly at closed boundaries (`signal_close_ms = open_ms + 900_000 - 1`, aligned to closed 1H `open_ms + 3_600_000 - 1` and closed 4H `open_ms + 14_400_000 - 1`) |
| Warmup Requirement | Minimum **3,400** closed M15 bars (`warmup_bars=3400` in `strategy.spec()` = 850 1H bars = ~212 4H bars, covering 4H `EMA(150)`, 4H `EMA(100)`, 1H `KAMA(40)`, and 1H `ATR(14)/SMA(50)`). Note: C15 V5 alone stabilizes after `14 + 50 = 64` 1H bars (256 M15 bars). |

## 5. Output Schema (`autotrade_v3.engine.signal_engine.Signal`)

Each returned item is an immutable `Signal` dataclass with 23 fields:
- Identity/timing: `symbol` (`str`), `combo_id` (`int`), `combo_name` (`str`), `timeframe` (`"15m"|"1h"`), `side` (`"BUY"|"SELL"`), `bar_position` (`int`), `bar_open_ms` (`int`), `signal_close_ms` (`int`), `signal_close_dt` (`pd.Timestamp` UTC), `signal_id` (`str`, format `SIG_<SYM>_<CNAME>_<TF>_<SIDE>_<CLOSE_MS>`).
- Entry & Fixed Exit reference: `entry_price` (`float`, closed bar close), `tp_pct` (`float`), `sl_pct` (`float`), `tp_price` (`float`), `sl_price` (`float`).
- L2 admission metadata: `approved` (`bool`), `warmup_ready` (`bool`), `smc_dir` (`int`), `ob_blocked` (`bool`), `is_new6` (`bool`), `provider` (`str`).
- Policy attachment: `exit_policy` (`ExitPolicy` dataclass with `split_legs=False`, `trailing_act_pct=0.0`, `trailing_cb_pct=0.0`, `leg_weights=(1.0,)`) and `exit_policy_hash` (`str` SHA-256).

## 6. ETH-C15 V5 Formula & Full 30-Combo Specification

### ETH-USDT/C15 (`combo_id=105`) — `V5_SELL_ONLY_ATR140`
- **Timeframe:** `1h` (computed from closed M15 bars via `resample_ohlcv(ensure_dt(df), "1h")`).
- **Base indicator (Layer 1):** `KAMA_ER(40, 2, 40)` with `er_min = 0.4`.
- **Layer 2 Baseline:** `OB` (`ob_context` pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5).
- **BUY rule:** **DISABLED** (`C15V5SellAtr140Filter` rejects all BUY signals with `BLOCKED_V5_BUY_DISABLED`).
- **SELL rule:** Original C15 `KAMA_ER` SELL + `OB` pass **AND** `ATR(14)_1H / SMA(ATR(14)_1H, 50) <= 1.40` (where `atr(df_1h, 14)` uses Wilder ATR and `SMA(..., 50)` uses 50-bar simple rolling mean on closed 1H bars). Fails closed on missing/non-finite ratio.
- **Exit policy:** Fixed `TP = 2.0%` (`0.02`), `SL = 0.8%` (`0.008`), `split_legs=False`, zero trailing, zero post-TP cooldown.

### All 30 Combos (`V3_LAB_5@5.0.0`, 29/30 Identical to `V3_LAB_4@4.1.1`)

| # | Combo Key | ID | TF | Execution Version | Layer 2 | TP / SL | Exit Mode |
| ---: | :--- | ---: | :--- | :--- | :--- | :--- | :--- |
| 1 | `BTC-USDT/C1` | 1 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_STRICT` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 2 | `BTC-USDT/C2` | 2 | `1h` | `H1_SELL_ONLY` | `SMC_VETO_OB` | `1.20% / 0.8000%` | Fixed (No trailing) |
| 3 | `BTC-USDT/C4` | 4 | `1h` | `H1_BUY_ONLY` | `SMC_VETO` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 4 | `BTC-USDT/C6` | 6 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_VETO_OB` | `2.00% / 0.6667%` | Fixed (No trailing) |
| 5 | `BTC-USDT/C7` | 7 | `15m` | `H1_BUY_ONLY` | `OFF` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 6 | `BTC-USDT/C9` | 9 | `15m` | `20FINAL_FIXED_V1@1` | `SMC_VETO` | `1.00% / 0.4000%` | Fixed (No trailing) |
| 7 | `BTC-USDT/C10` | 10 | `15m` | `20FINAL_FIXED_V1@1` | `SMC_VETO` | `1.00% / 0.8000%` | Fixed (No trailing) |
| 8 | `BTC-USDT/TIER` | 11 | `1h` | `20FINAL_FIXED_V1@1` | `OFF` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 9 | `BTC-USDT/C11` | 101 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_STRICT_OB` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 10 | `BTC-USDT/C14` | 104 | `1h` | `BTC-USDT_C14_H3a_KUMO_BAND_V1` | `OB` | `2.00% / 0.6667%` | Fixed (No trailing) |
| 11 | `BTC-USDT/C16` | 106 | `1h` | `H3_VORTEX_RATIO_1_22` | `OB` | `1.10% / 0.9000%` | Fixed (No trailing) |
| 12 | `ETH-USDT/C1` | 1 | `1h` | `H1_BOTH_1` | `SMC_VETO` | `2.00% / 0.6667%` | Fixed (No trailing) |
| 13 | `ETH-USDT/C3` | 3 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_STRICT` | `1.80% / 0.6000%` | Fixed (No trailing) |
| 14 | `ETH-USDT/C4` | 4 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_VETO` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 15 | `ETH-USDT/C6` | 6 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_VETO` | `2.00% / 0.6667%` | Fixed (No trailing) |
| 16 | `ETH-USDT/C7` | 7 | `15m` | `20FINAL_FIXED_V1@1` | `OB` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 17 | `ETH-USDT/C10` | 10 | `15m` | `H3_ASYMMETRIC` | `BUY=SMC_VETO/SELL=SMC_STRICT` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 18 | `ETH-USDT/C12` | 102 | `1h` | `H7_SELL_4H_TREND_ONLY` | `SMC_VETO_OB` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 19 | `ETH-USDT/C13` | 103 | `1h` | `H5_SELL_ONLY_4H_TREND` | `OFF` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 20 | `ETH-USDT/C15` | 105 | `1h` | `V5_SELL_ONLY_ATR140` | `OB` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 21 | `ETH-USDT/C2` | 2 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_VETO_OB` | `1.20% / 0.8000%` | Fixed (No trailing) |
| 22 | `BTC-USDT/C3` | 3 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_STRICT` | `1.80% / 0.6000%` | Fixed (No trailing) |
| 23 | `ETH-USDT/C9` | 9 | `15m` | `20FINAL_FIXED_V1@1` | `SMC_VETO` | `1.00% / 0.4000%` | Fixed (No trailing) |
| 24 | `ETH-USDT/TIER` | 11 | `1h` | `20FINAL_FIXED_V1@1` | `OFF` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 25 | `ETH-USDT/C11` | 101 | `1h` | `H3_B1_DEMARKER` | `SMC_STRICT_OB` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 26 | `BTC-USDT/C12` | 102 | `1h` | `20FINAL_FIXED_V1@1` | `SMC_VETO_OB` | `1.80% / 0.9000%` | Fixed (No trailing) |
| 27 | `BTC-USDT/C13` | 103 | `1h` | `H4_SELL_ONLY_4H_TREND` | `OFF` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 28 | `ETH-USDT/C14` | 104 | `1h` | `A-R2-05_ATR_SL_ADJUSTMENT` | `OB` | `2.00% / 0.6667%` | Fixed (No trailing) |
| 29 | `BTC-USDT/C15` | 105 | `1h` | `20FINAL_FIXED_V1@1` | `OB` | `2.00% / 0.8000%` | Fixed (No trailing) |
| 30 | `ETH-USDT/C16` | 106 | `1h` | `20FINAL_FIXED_V1@1` | `OB` | `1.10% / 0.9000%` | Fixed (No trailing) |

> Note on `ETH-USDT/C14`: signal admission requires `0.005 <= abs(span_a - span_b) / close_1h < 0.010`, and runtime SL is `clamp(1.0 * ATR(14)_1H / close_1H, 0.006, 0.012)` per signal (Fixed TP=2.0%, no trailing).

## 7. Lab vs Live Registry Boundaries (Do Not Change Live Combo Count)

| Surface | Registry ID | File / Path | Combos | Scope / Notes |
| :--- | :--- | :--- | ---: | :--- |
| **Lab (`v3_lab`)** | `V3_LAB_5@5.0.0` | `autotrade_v3/lab/v3_lab_5_config.json` | 30 | Research standard integrating `V5_SELL_ONLY_ATR140` on `ETH-USDT/C15` plus prior R1/Batch-A/R2 upgrades. `live_eligible=False`, `top20_live_modified=False`. |
| **Live (`origin/main`)** | `V3_LIVE_20_POSITIVE_FIXED_V1` | `autotrade_v3/live/registries/V3_LIVE_20_POSITIVE_FIXED_V1.json` | 20 | Untouched by this handoff. Currently runs `ETH-USDT/C1` (`H1_BOTH_1`), 19 `BASELINE` combos (including `ETH-USDT/105`), and `new6_signal_mode="virtual_position"`. |

**Guidance for Claude Live integration:**
1. Keep Live combo count strictly at **20** unless Owner explicitly approves a separate registry promotion.
2. Notice the difference between Lab `30` combos (`V3_LAB_5@5.0.0`) and Live `20` combos (`V3_LIVE_20_POSITIVE_FIXED_V1`):
   - Live (`V3_LIVE_20_POSITIVE_FIXED_V1`) currently sets 18 non-C1/non-C15 combos to `BASELINE` and applies `new6_signal_mode="virtual_position"` (`C11`, `C13`, `C15`, `C16`).
   - If promoting **only** `ETH-USDT/C15` (`V5_SELL_ONLY_ATR140`) onto the existing 20-combo Live registry, Claude should reuse `autotrade_v3/filters/c15_v5_sell_atr140.py` (`C15V5SellAtr140Filter`) on Live's C15 stream so Live & Lab share one exact C15 V5 implementation (while noting Live's `new6_signal_mode="virtual_position"` vs Lab's bar-level signal emission).
   - Alternatively, if Owner promotes a subset of `V3_LAB_5@5.0.0` combos directly, call `strategy.generate_signals_for_combos(data, allowed_combos=LIVE_ALLOWED_COMBOS)`.

## 8. Verification Commands, Parity Fixture & Rollback

1. Run L2 unit, AST, simulator-independence, no-lookahead & fixture parity tests:
   ```bash
   pytest -q autotrade_v3/tests/test_v3_lab_5_integration.py
   ```
2. Verify Live adapter parity against `autotrade_v3/tests/fixtures/v3_lab_5_l2_parity_fixture.json`:
   - `approved_signal_count = 730`, `raw_signal_count = 910`
   - `c15_ob_approved_input_count = 268`, `c15_v5_kept_count = 189`
   - `c15_v5_dropped_reason_counts = {"BLOCKED_V5_BUY_DISABLED": 76, "BLOCKED_V5_SELL_ATR_RATIO_GT_140": 3}`
   - `approved_signals_sha256` & `spec_hash=631cd361bc0a1100675e4c77c783ae64952854c6f286694bf71306329227372e` must match cleanly.
3. Rollback path: if any issue arises, revert/select `StrategyRegistry.get("V3_LAB_4", "4.1.1")` (which remains completely unmodified in this repo) or keep `V3_LIVE_20_POSITIVE_FIXED_V1` with `ETH-USDT/105` `BASELINE`.

## 9. Non-OOS Disclosure & Mandatory Shadow Check

- Historical 3Y (`2023-11-01`..`<2026-11-01`) numbers (OTPC `IDEAL`: 186 / `+$48.23`, `BASE`: 187 / `+$43.60`, `STRESS`: 191 / `+$20.19`; 30-combo Portfolio `BASE`: `+$320.19`, `STRESS`: `-$4.66`) are observed historical research backtests (`EVAL` was viewed during H0–H3 & Round 2 research and is not an independent OOS split).
- Shadow/forward verification on post-`2026-11-01` data is required before full capital promotion in Live.
