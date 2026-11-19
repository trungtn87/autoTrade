"""Research variant: 20FINAL_FIXED_TPSL with ETH-USDT/C1 SELL CCI sign corrected (C < -80).

Does NOT modify 20FINAL@1, 20final_v1.json, Golden Baseline, or live engines.
Only changes `shortc` for ETH-USDT/C1 from `C > -80` to `C < -80`.
All BUY conditions, other combos, TP/SL, and FIXED_TP_SL exit rules remain identical.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.indicators import (
    atr,
    cci,
    dmi,
    edge_event,
    ema,
    mfi,
    rsi,
    sma,
    stochastic,
)
from autotrade_v3.strategy_core.indicators.signals import align_confirmed, ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_ob import OBConfig, ob_context
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.strategy_core.indicators.two_trail_layer12 import approved
from scripts.fixed_tpsl_strategy import FixedTpSl20FinalStrategy

M15_MS = 15 * 60 * 1000


class C1SellCorrectedFixedTpSlStrategy(FixedTpSl20FinalStrategy):
    """20FINAL_FIXED_TPSL with ETH-USDT/C1 SELL formula corrected to C < -80."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._strategy_id_override = "20FINAL_FIXED_TPSL_C1_SELL_CORR_VETO"
        self._version_override = "1.0.0"

    def _eth_c1_spec(self):
        matches = [
            c for c in self._core.spec().combos
            if c.symbol == "ETH-USDT" and c.combo_id == 1
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected 1 ETH-USDT/C1 ComboSpec, found {len(matches)}")
        return matches[0]

    def get_exit_spec_hash(self) -> str:
        # Exit policies are 100% identical to FixedTpSl20FinalStrategy
        orig_sid = self._strategy_id_override
        self._strategy_id_override = "20FINAL_FIXED_TPSL"
        try:
            return super().get_exit_spec_hash()
        finally:
            self._strategy_id_override = orig_sid

    def get_signal_spec_hash(self) -> str:
        eth_c1 = self._eth_c1_spec()
        return canonical_json_sha256(
            {
                "base_signal_spec_hash": super().get_signal_spec_hash(),
                "patch": f"ETH-USDT/C1 SELL shortc: C < -80 (was C > -80), layer2={eth_c1.layer2}",
                "strategy_id": self._strategy_id_override,
            }
        )

    def get_spec_hash(self) -> str:
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    def _generate_eth_c1_sell_corrected(
        self,
        df_m15: pd.DataFrame,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate ETH-USDT/C1 SELL signals with `C < -80`, matching Final20Strategy schema."""
        d15 = ensure_dt(df_m15)
        d1 = resample_ohlcv(d15, "1h")
        d4 = resample_ohlcv(d15, "4h")

        A21 = atr(d1, 21)
        R = rsi(d1.close, 14)
        M = mfi(d1, 14)
        _, _, ADX = dmi(d1, 14, 14)
        volma = sma(d1.volume, 20)
        K = stochastic(d1.close, d1.high, d1.low, 11)
        D = sma(K, 3)
        C = cci(d1.close, d1.high, d1.low, 9)
        ema150_4h = align_confirmed(ema(d4.close, 150), d1.index, "4h")

        macd = ema(d1.close, 8) - ema(d1.close, 21)
        sig_m = ema(macd, 9)
        hist = macd - sig_m

        volsp = d1.volume > volma * 1.5
        adxr = ADX > ADX.shift(1)
        ss08 = (d1.close < d1.open) & ((d1.open - d1.close) > A21 * 0.8)
        td = d1.close < ema150_4h

        eth_c1 = self._eth_c1_spec()
        mfi_short = float(eth_c1.entry.get("mfi_short", 50.0))

        # CORRECTED: C < -80 (instead of C > -80)
        shortc = (K < 70) & (K < D) & (C < -80) & (C < C.shift(1))
        S = shortc & (R < 45) & (hist < 0) & (M < mfi_short) & adxr & ss08 & td & volsp
        ev_s = edge_event(S)

        smc1 = smc_direction(d1, 50, False)
        obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
        ob1 = ob_context(d1, obcfg)
        posmap = {ts: i for i, ts in enumerate(d15.index)}

        ep = self.get_exit_policy(eth_c1.symbol, eth_c1.combo_id)
        ep_hash = ep.get_policy_hash()
        warmup_bars = self._core.spec().warmup_bars

        out: list[Signal] = []
        for ot in ev_s.index[ev_s.fillna(False)]:
            last = ot + pd.Timedelta(minutes=45)
            if last not in posmap:
                continue
            pos = posmap[last]
            sd = int(smc1.loc[ot])
            blocked = bool(ob1.loc[ot]["sell_blocked"])
            ok = bool(approved("S", sd, blocked, eth_c1.layer2))
            warmup_ok = pos >= (warmup_bars - 1)
            if only_approved and not ok:
                continue
            if only_warmup_ready and not warmup_ok:
                continue

            close_ms = int(d15.index[pos].value // 1_000_000 + M15_MS - 1)
            bar_open_ms = int(d15.index[pos].value // 1_000_000)
            ref_price = float(d15.iloc[pos]["close"])
            provider = str(d15.iloc[pos].get("provider", "UNKNOWN"))
            sig_id = f"SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_SELL_{close_ms}"
            out.append(
                Signal(
                    symbol=eth_c1.symbol,
                    combo_id=eth_c1.combo_id,
                    combo_name=eth_c1.combo_name,
                    timeframe=eth_c1.native_timeframe,
                    bar_position=pos,
                    bar_open_ms=bar_open_ms,
                    signal_close_ms=close_ms,
                    signal_close_dt=d15.index[pos] + pd.Timedelta(milliseconds=M15_MS - 1),
                    side="SELL",
                    entry_price=ref_price,
                    tp_price=ref_price * (1.0 - ep.tp_pct),
                    sl_price=ref_price * (1.0 + ep.sl_pct),
                    tp_pct=ep.tp_pct,
                    sl_pct=ep.sl_pct,
                    approved=ok,
                    warmup_ready=warmup_ok,
                    smc_dir=sd,
                    ob_blocked=blocked,
                    is_new6=eth_c1.is_new6,
                    provider=provider,
                    signal_id=sig_id,
                    exit_policy=ep,
                    exit_policy_hash=ep_hash,
                )
            )
        return out

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        base_sigs = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        target_symbols = symbols or list(data.keys())
        if "ETH-USDT" not in target_symbols or "ETH-USDT" not in data:
            return base_sigs

        # Remove ONLY original ETH-USDT C1 SELL signals, keep everything else identically
        kept = [
            s for s in base_sigs
            if not (s.symbol == "ETH-USDT" and s.combo_id == 1 and s.side == "SELL")
        ]
        new_eth_c1_sells = self._generate_eth_c1_sell_corrected(
            data["ETH-USDT"],
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        combined = kept + new_eth_c1_sells
        combined.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return combined
