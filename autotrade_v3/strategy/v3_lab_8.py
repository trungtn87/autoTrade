"""Official versioned pure candle-only strategy implementation for V3_LAB_8 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_8@8.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_7@7.0.0 (including ETH-USDT/C7, C12, C15).
- Leaves 27/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_7@7.0.0.
- Integrates 3 upgraded combos:
  1. `ETH-USDT/C6` (`combo_id=6`): `R2_V4_COMBINED`
     * BUY: BODY_1.8 H0 (Layer2 SMC_VETO, Fixed TP 2.0% / SL 0.666667%, trailing OFF) + `ATR14_1H / SMA50(ATR14_1H) >= 1.2`.
     * SELL: CCI Audit SELL (`C < -100`) + `ATR14_1H / SMA50(ATR14_1H) >= 0.9`, Fixed TP 2.0% / SL 0.666667%, trailing OFF.
  2. `BTC-USDT/C1` (`combo_id=1`): `H2_BUY_VOLUME_1_8` (Batch C P2)
     * BUY: volume > 1.8 * SMA20, SELL keeps 1.5 * SMA20, 1H, SMC_STRICT, TP 2.0% / SL 0.8%.
  3. `BTC-USDT/C11` (`combo_id=101`): `H1_BUY_0_78` (Batch C P2)
     * DeMarker(60) BUY > 0.78 / SELL < 0.25, 1H, SMC_STRICT_OB, TP 1.8% / SL 0.9%.
"""
from __future__ import annotations

from collections.abc import Collection
import dataclasses
from pathlib import Path

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal, signal_from_event
from autotrade_v3.filters.c6_r2_v4_combined import (
    C6_COMBO_ID,
    C6_SYMBOL,
    C6R2V4CombinedFilter,
    build_c6_atr_ratio_lookup,
    generate_c6_cci_audit_sell_signals,
)
from autotrade_v3.strategy.v3_lab_7 import V3Lab7Strategy
from autotrade_v3.strategy_core.indicators.indicators import ema
from autotrade_v3.strategy_core.indicators.layer3_long import edge_event, precompute
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_ob import OBConfig, ob_context
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.strategy_core.indicators.two_trail_layer12 import approved
from autotrade_v3.strategy_core.strategies.final20.logic import _make_signal_event


class V3Lab8Strategy(V3Lab7Strategy):
    """Official V3_LAB_8@8.0.0 strategy (V3_LAB_7 + ETH-C6 R2_V4_COMBINED + BTC-C1 H2 + BTC-C11 H1)."""

    strategy_id: str = "V3_LAB_8"
    version: str = "8.0.0"
    SUPPORTED_VERSIONS = ("8.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "8.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)
        self._apply_btc_c11_h1_override()

    def _apply_btc_c11_h1_override(self) -> None:
        """Apply Batch C P2 H1_BUY_0_78 (DeMarker 60, long_min=0.78, short_max=0.25) to BTC-USDT/C11."""
        core = self._engine._core
        spec = core.spec()
        new_combos = []
        for c in spec.combos:
            if c.symbol == "BTC-USDT" and int(c.combo_id) == 101:
                new_entry = dict(c.entry)
                new_entry["indicator"] = "DeMarker"
                new_entry["period"] = 60
                new_entry["long_min"] = 0.78
                new_entry["short_max"] = 0.25
                c = dataclasses.replace(c, entry=new_entry)
            new_combos.append(c)
        new_spec = dataclasses.replace(spec, combos=tuple(new_combos))
        core._spec = new_spec  # type: ignore[attr-defined]
        self._core = core
        self._engine._refresh_cases()

    def _regenerate_btc_c1_buy_vol_1_8(
        self,
        btc_m15: pd.DataFrame,
        *,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Regenerate BUY signals for BTC-USDT/C1 with H2_BUY_VOLUME_1_8 (vol > 1.8 * SMA20)."""
        d15 = ensure_dt(btc_m15).tz_convert("UTC")
        pc = precompute(d15, None)
        d1 = pc["d1"]
        p1 = pc["1"]
        c_spec = next(c for c in self.spec().combos if c.symbol == "BTC-USDT" and int(c.combo_id) == 1)
        ep = self.get_exit_policy("BTC-USDT", 1)
        ep_hash = ep.get_policy_hash()
        warmup_bars = int(self.spec().warmup_bars)
        posmap = {ts: i for i, ts in enumerate(d15.index)}

        A21, R, M, ADX, K, D, C = p1["A21"], p1["R"], p1["M"], p1["ADX"], p1["K"], p1["D"], p1["C"]
        macd = ema(d1.close, 8) - ema(d1.close, 21)
        hist = macd - ema(macd, 9)
        volsp = d1.volume > p1["volma"] * 1.8
        adxr = ADX > ADX.shift(1)
        sb08 = (d1.close > d1.open) & ((d1.close - d1.open) > A21 * 0.8)
        tu = d1.close > p1["ema1504"]
        longc = (K > 30) & (K > D) & (C > 80) & (C > C.shift(1))
        lth = float(c_spec.entry.get("mfi_long", 50.0))
        L = edge_event(longc & (R > 55) & (hist > 0) & (M > lth) & adxr & sb08 & tu & volsp)
        smc_s = smc_direction(d1, 50, False)
        ob_df = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))
        offset = pd.Timedelta(minutes=45)

        out: list[Signal] = []
        for ot in L.index[L.fillna(False)]:
            last = ot + offset
            if last not in posmap:
                continue
            sd = int(smc_s.loc[ot])
            blocked = bool(ob_df.loc[ot, "buy_blocked"])
            ok = bool(approved("L", sd, blocked, c_spec.layer2))
            ev = _make_signal_event("BTC-USDT", c_spec, "L", ot, posmap[last], sd, blocked, ok, d15, warmup_bars)
            if only_approved and not ev.tags["approved"]:
                continue
            if only_warmup_ready and not ev.tags["warmup_ready"]:
                continue
            out.append(
                dataclasses.replace(
                    signal_from_event(ev, c_spec),
                    exit_policy=ep,
                    exit_policy_hash=ep_hash,
                )
            )
        return out

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        base_signals = super().generate_signals(
            data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        target_syms = set(symbols) if symbols is not None else None
        out: list[Signal] = list(base_signals)

        # 1. BTC-USDT/C1 H2_BUY_VOLUME_1_8
        if target_syms is None or "BTC-USDT" in target_syms:
            btc_df = data.get("BTC-USDT")
            if btc_df is not None and not btc_df.empty:
                out = [s for s in out if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 1 and s.side == "BUY")]
                out.extend(
                    self._regenerate_btc_c1_buy_vol_1_8(
                        btc_df,
                        only_approved=only_approved,
                        only_warmup_ready=only_warmup_ready,
                    )
                )

        # 2. ETH-USDT/C6 R2_V4_COMBINED
        if target_syms is None or C6_SYMBOL in target_syms:
            eth_df = data.get(C6_SYMBOL)
            if eth_df is not None and not eth_df.empty:
                eth_dt = ensure_dt(eth_df)
                eth_1h = resample_ohlcv(eth_dt, "1h")
                close_ms_1h, atr_ratio_50 = build_c6_atr_ratio_lookup(eth_1h)
                c6_filter = C6R2V4CombinedFilter(close_ms_1h, atr_ratio_50)

                filtered: list[Signal] = []
                for sig in out:
                    if sig.symbol == C6_SYMBOL and int(sig.combo_id) == C6_COMBO_ID:
                        dec = c6_filter.evaluate_signal(sig)
                        if dec.action != "KEEP":
                            if only_approved:
                                continue
                            sig = Signal(
                                **{
                                    **sig.__dict__,
                                    "approved": False,
                                    "rejection_reason": dec.reason,
                                }
                            )
                    filtered.append(sig)
                out = filtered

                cfg = self.get_combo_config(C6_SYMBOL, C6_COMBO_ID)
                ep = self.get_exit_policy(C6_SYMBOL, C6_COMBO_ID)
                sell_candidates = generate_c6_cci_audit_sell_signals(
                    eth_dt,
                    combo_name=str(cfg.get("name", "C6")),
                    tp_pct=float(cfg["tp_pct"]),
                    sl_pct=float(cfg["sl_pct"]),
                    strategy_id=self.strategy_id,
                    exit_policy=ep,
                    exit_policy_hash=ep.get_policy_hash(),
                    warmup_end_ms=None,
                )
                for s_sig in sell_candidates:
                    dec = c6_filter.evaluate_signal(s_sig)
                    if dec.action != "KEEP":
                        if only_approved:
                            continue
                        s_sig = Signal(
                            **{
                                **s_sig.__dict__,
                                "approved": False,
                                "rejection_reason": dec.reason,
                            }
                        )
                    out.append(s_sig)

        out.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
        return out
