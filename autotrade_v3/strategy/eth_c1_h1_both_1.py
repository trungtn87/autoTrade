"""ETH-USDT/C1 L2 H1_BOTH_1 Strategy Integration for AutoTrade V3.

Integrates the P3-approved H1_BOTH_1 L2 filter exclusively for `ETH-USDT/C1`:
- BUY:  existing 20FINAL & SMC_VETO AND `ema150_4h_slope_10bar_pct <= 1.5`
- SELL: C1 SELL (`C < -80`) & SMC_VETO AND `dist_ema150_4h_pct >= -6.0`
- Exit: TP 2.0%, SL 0.666667%, no trailing (`split_legs=False`, `leg_weights=(1.0,)`).

All other 19 combos (BTC-USDT/* and ETH-USDT/C3..C106) and the locked
`20FINAL@1` Golden Baseline remain completely unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any, Literal

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
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

logger = logging.getLogger(__name__)

M15_MS = 15 * 60 * 1000

ETH_C1_STRATEGY_ID = "20FINAL_FIXED_TPSL_ETH_C1_H1_BOTH_1"
ETH_C1_STRATEGY_VERSION = "1.0.0"

H1_BUY_MAX_SLOPE_4H_PCT = 1.5
H1_SELL_MIN_DIST_EMA150_4H_PCT = -6.0

REASON_H1_BUY_EMA_SLOPE = "H1_BUY_EMA_SLOPE"
REASON_H1_SELL_EMA_DISTANCE = "H1_SELL_EMA_DISTANCE"
REASON_SMC_VETO = "SMC_VETO"

_REQUIRED_OHLCV_COLS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class L2Decision:
    """Result of evaluating L2 (SMC_VETO + optional H1 filter) on a candidate signal."""

    approved: bool
    reason: str | None = None


def compute_eth_c1_l2_features(df_m15: pd.DataFrame) -> dict[str, Any]:
    """Compute causal 1H and confirmed 4H EMA150 features for ETH-USDT/C1 from M15 OHLCV.

    Uses `align_confirmed(..., d1.index, "4h")` so at 1H bar open `ot` (closing at
    `ot + 1h`), only 4H bars already closed at or before `ot` are visible.
    """
    if not isinstance(df_m15, pd.DataFrame) or df_m15.empty:
        raise ValueError("ETH-USDT M15 candle DataFrame is missing or empty.")
    missing_cols = [c for c in _REQUIRED_OHLCV_COLS if c not in df_m15.columns]
    if missing_cols:
        raise ValueError(f"ETH-USDT M15 DataFrame missing required OHLCV columns: {missing_cols}")

    d15 = ensure_dt(df_m15)
    d1 = resample_ohlcv(d15, "1h")
    d4 = resample_ohlcv(d15, "4h")
    if d1.empty or d4.empty:
        raise ValueError("Resampled 1H or 4H DataFrame is empty for ETH-USDT.")

    ema150_4h_raw = ema(d4.close, 150)
    prev_ema150_4h = ema150_4h_raw.shift(10)
    slope_10bar_raw = (ema150_4h_raw - prev_ema150_4h) / prev_ema150_4h * 100.0

    ema150_4h_1h = align_confirmed(ema150_4h_raw, d1.index, "4h")
    slope_10bar_1h = align_confirmed(slope_10bar_raw, d1.index, "4h")
    dist_ema150_4h_pct = (d1.close - ema150_4h_1h) / ema150_4h_1h * 100.0

    a21 = atr(d1, 21)
    r14 = rsi(d1.close, 14)
    m14 = mfi(d1, 14)
    _, _, adx14 = dmi(d1, 14, 14)
    volma20 = sma(d1.volume, 20)
    k11 = stochastic(d1.close, d1.high, d1.low, 11)
    d3 = sma(k11, 3)
    c9 = cci(d1.close, d1.high, d1.low, 9)
    macd = ema(d1.close, 8) - ema(d1.close, 21)
    sig_m = ema(macd, 9)
    hist = macd - sig_m

    smc1 = smc_direction(d1, 50, False)
    ob1 = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))
    posmap = {ts: i for i, ts in enumerate(d15.index)}

    return {
        "d15": d15,
        "d1": d1,
        "d4": d4,
        "a21": a21,
        "ema150_4h_1h": ema150_4h_1h,
        "slope_10bar_1h": slope_10bar_1h,
        "dist_ema150_4h_pct": dist_ema150_4h_pct,
        "r14": r14,
        "m14": m14,
        "adx14": adx14,
        "volma20": volma20,
        "k11": k11,
        "d3": d3,
        "c9": c9,
        "hist": hist,
        "smc1": smc1,
        "ob1": ob1,
        "posmap": posmap,
    }


def evaluate_eth_c1_l2(
    side: Literal["BUY", "SELL"],
    smc_dir: int,
    ob_blocked: bool,
    layer2: str,
    *,
    slope_10bar_pct: float | None = None,
    dist_ema150_4h_pct: float | None = None,
    enable_h1: bool = True,
    buy_max_slope_4h_pct: float = H1_BUY_MAX_SLOPE_4H_PCT,
    sell_min_dist_ema150_4h_pct: float = H1_SELL_MIN_DIST_EMA150_4H_PCT,
) -> L2Decision:
    """Evaluate L2 (existing SMC_VETO/layer2 AND H1_BOTH_1) for ETH-USDT/C1.

    Raises ValueError if H1 is enabled and the required indicator value is missing or non-finite.
    """
    side_norm = str(side).upper()
    if side_norm not in ("BUY", "SELL"):
        raise ValueError(f"Unsupported side for ETH-USDT/C1 L2 evaluation: {side!r}")

    side_code = "L" if side_norm == "BUY" else "S"
    if not bool(approved(side_code, int(smc_dir), bool(ob_blocked), str(layer2))):
        return L2Decision(approved=False, reason=str(layer2) or REASON_SMC_VETO)

    if not enable_h1:
        return L2Decision(approved=True, reason=None)

    if side_norm == "BUY":
        if slope_10bar_pct is None or not math.isfinite(float(slope_10bar_pct)):
            raise ValueError(
                "Missing or non-finite ema150_4h_slope_10bar_pct for ETH-USDT/C1 BUY L2 check."
            )
        if float(slope_10bar_pct) > float(buy_max_slope_4h_pct):
            return L2Decision(approved=False, reason=REASON_H1_BUY_EMA_SLOPE)
        return L2Decision(approved=True, reason=None)

    if dist_ema150_4h_pct is None or not math.isfinite(float(dist_ema150_4h_pct)):
        raise ValueError(
            "Missing or non-finite dist_ema150_4h_pct for ETH-USDT/C1 SELL L2 check."
        )
    if float(dist_ema150_4h_pct) < float(sell_min_dist_ema150_4h_pct):
        return L2Decision(approved=False, reason=REASON_H1_SELL_EMA_DISTANCE)
    return L2Decision(approved=True, reason=None)


class EthC1H1Both1Strategy(Baseline20FinalStrategy):
    """20FINAL with H1_BOTH_1 L2 filter integrated exclusively on ETH-USDT/C1.

    - ETH-USDT/C1 BUY:  `ema150_4h_slope_10bar_pct <= 1.5` (pre-edge, matching P3)
    - ETH-USDT/C1 SELL: `C < -80` AND `dist_ema150_4h_pct >= -6.0` (pre-edge, matching P3)
    - ETH-USDT/C1 Exit: TP 2.0%, SL 0.666667%, no trailing (`split_legs=False`)
    - Other 19 combos: identical signals to `20FINAL@1`.
    - Set `enable_eth_c1_h1_l2=False` for immediate rollback to L2 without H1.
    """

    def __init__(
        self,
        *,
        enable_eth_c1_h1_l2: bool = True,
        buy_max_slope_4h_pct: float = H1_BUY_MAX_SLOPE_4H_PCT,
        sell_min_dist_ema150_4h_pct: float = H1_SELL_MIN_DIST_EMA150_4H_PCT,
        pre_edge_h1: bool = True,
        **kwargs: Any,
    ):
        kwargs.setdefault("strategy_id_override", ETH_C1_STRATEGY_ID)
        kwargs.setdefault("version_override", ETH_C1_STRATEGY_VERSION)
        super().__init__(**kwargs)
        self.enable_eth_c1_h1_l2 = bool(enable_eth_c1_h1_l2)
        self.buy_max_slope_4h_pct = float(buy_max_slope_4h_pct)
        self.sell_min_dist_ema150_4h_pct = float(sell_min_dist_ema150_4h_pct)
        self.pre_edge_h1 = bool(pre_edge_h1)
        self.last_l2_rejections: list[dict[str, Any]] = []

    def _eth_c1_spec(self):
        matches = [
            c for c in self._core.spec().combos
            if c.symbol == "ETH-USDT" and c.combo_id == 1
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected 1 ETH-USDT/C1 ComboSpec, found {len(matches)}")
        return matches[0]

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        ep = super().get_exit_policy(symbol, combo_id)
        if symbol == "ETH-USDT" and int(combo_id) == 1:
            return ExitPolicy(
                tp_pct=ep.tp_pct,
                sl_pct=ep.sl_pct,
                split_legs=False,
                trailing_act_pct=0.0,
                trailing_cb_pct=0.0,
                leg_weights=(1.0,),
                max_bars_held=ep.max_bars_held,
                force_close_on_window_end=ep.force_close_on_window_end,
            )
        return ep

    def get_signal_spec_hash(self) -> str:
        eth_c1 = self._eth_c1_spec()
        return canonical_json_sha256(
            {
                "base_signal_spec_hash": super().get_signal_spec_hash(),
                "strategy_id": self.strategy_id,
                "version": self.version,
                "eth_c1_sell_cci": "C < -80",
                "eth_c1_layer2": eth_c1.layer2,
                "enable_eth_c1_h1_l2": self.enable_eth_c1_h1_l2,
                "buy_max_slope_4h_pct": self.buy_max_slope_4h_pct,
                "sell_min_dist_ema150_4h_pct": self.sell_min_dist_ema150_4h_pct,
                "pre_edge_h1": self.pre_edge_h1,
            }
        )

    def get_exit_spec_hash(self) -> str:
        spec = self._core.spec()
        policies = {
            c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict()
            for c in sorted(spec.combos, key=lambda x: x.combo_key)
        }
        return canonical_json_sha256(
            {
                "strategy_id": self.strategy_id,
                "version": self.version,
                "exit_policies": policies,
            }
        )

    def get_spec_hash(self) -> str:
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    def get_config_hash(self) -> str:
        return self.get_spec_hash()

    def _record_l2_rejection(
        self,
        *,
        signal_id: str,
        side: Literal["BUY", "SELL"],
        signal_close_ms: int,
        reason: str,
        slope_val: float | None,
        dist_val: float | None,
    ) -> None:
        rec = {
            "signal_id": signal_id,
            "symbol": "ETH-USDT",
            "combo_id": 1,
            "combo_name": "C1",
            "side": side,
            "signal_close_ms": int(signal_close_ms),
            "reason": reason,
            "ema150_4h_slope_10bar_pct": slope_val,
            "dist_ema150_4h_pct": dist_val,
        }
        self.last_l2_rejections.append(rec)
        logger.info(
            "ETH-USDT/C1 L2 rejected %s signal_id=%s reason=%s slope_4h=%s dist_4h=%s",
            side,
            signal_id,
            reason,
            slope_val,
            dist_val,
        )

    def _generate_eth_c1_side(
        self,
        features: dict[str, Any],
        *,
        side: Literal["BUY", "SELL"],
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate ETH-USDT/C1 signals for one side with SMC_VETO + H1_BOTH_1 L2."""
        required_keys = (
            "d15", "d1", "a21", "r14", "m14", "adx14", "volma20",
            "k11", "d3", "c9", "hist", "ema150_4h_1h",
            "slope_10bar_1h", "dist_ema150_4h_pct", "smc1", "ob1", "posmap",
        )
        missing_keys = [k for k in required_keys if k not in features]
        if missing_keys:
            raise KeyError(f"Missing ETH-USDT/C1 indicator features: {missing_keys}")

        d15 = features["d15"]
        d1 = features["d1"]
        a21 = features["a21"]
        r14 = features["r14"]
        m14 = features["m14"]
        adx14 = features["adx14"]
        volma20 = features["volma20"]
        k11 = features["k11"]
        d3 = features["d3"]
        c9 = features["c9"]
        hist = features["hist"]
        ema150_4h_1h = features["ema150_4h_1h"]
        slope_10bar_1h = features["slope_10bar_1h"]
        dist_ema150_4h_pct = features["dist_ema150_4h_pct"]
        smc1 = features["smc1"]
        ob1 = features["ob1"]
        posmap = features["posmap"]

        eth_c1 = self._eth_c1_spec()
        volsp = d1.volume > volma20 * 1.5
        adxr = adx14 > adx14.shift(1)

        if side == "BUY":
            mfi_long = float(eth_c1.entry.get("mfi_long", 50.0))
            sb08 = (d1.close > d1.open) & ((d1.close - d1.open) > a21 * 0.8)
            tu = d1.close > ema150_4h_1h
            longc = (k11 > 30) & (k11 > d3) & (c9 > 80) & (c9 > c9.shift(1))
            base_cond = longc & (r14 > 55) & (hist > 0) & (m14 > mfi_long) & adxr & sb08 & tu & volsp
            h1_series = slope_10bar_1h
            h1_ok_series = h1_series.notna() & (h1_series <= self.buy_max_slope_4h_pct)
            h1_reason = REASON_H1_BUY_EMA_SLOPE
        else:
            mfi_short = float(eth_c1.entry.get("mfi_short", 50.0))
            ss08 = (d1.close < d1.open) & ((d1.open - d1.close) > a21 * 0.8)
            td = d1.close < ema150_4h_1h
            shortc = (k11 < 70) & (k11 < d3) & (c9 < -80) & (c9 < c9.shift(1))
            base_cond = shortc & (r14 < 45) & (hist < 0) & (m14 < mfi_short) & adxr & ss08 & td & volsp
            h1_series = dist_ema150_4h_pct
            h1_ok_series = h1_series.notna() & (h1_series >= self.sell_min_dist_ema150_4h_pct)
            h1_reason = REASON_H1_SELL_EMA_DISTANCE

        ev_base = edge_event(base_cond)
        warmup_bars = self._core.spec().warmup_bars

        if self.enable_eth_c1_h1_l2:
            for ot in ev_base.index[ev_base.fillna(False)]:
                last = ot + pd.Timedelta(minutes=45)
                if last not in posmap:
                    continue
                pos = posmap[last]
                if pos >= (warmup_bars - 1):
                    val = float(h1_series.loc[ot])
                    ema_val = float(ema150_4h_1h.loc[ot])
                    if not math.isfinite(ema_val) or not math.isfinite(val):
                        raise ValueError(
                            f"Missing or non-finite 4H EMA150 indicator for ETH-USDT/C1 {side} at {ot}: "
                            f"ema150_4h={ema_val!r}, h1_metric={val!r}"
                        )
            ev = edge_event(base_cond & h1_ok_series) if self.pre_edge_h1 else (ev_base & h1_ok_series)
        else:
            ev = ev_base

        # Record H1 L2 rejections for post-warmup base edge events filtered out by H1
        if self.enable_eth_c1_h1_l2:
            for ot in ev_base.index[ev_base.fillna(False)]:
                last = ot + pd.Timedelta(minutes=45)
                if last not in posmap:
                    continue
                pos = posmap[last]
                if pos < (warmup_bars - 1):
                    continue
                if not bool(ev.loc[ot]):
                    close_ms = int(d15.index[pos].value // 1_000_000 + M15_MS - 1)
                    sig_id = f"SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_{side}_{close_ms}"
                    s_val = float(slope_10bar_1h.loc[ot]) if pd.notna(slope_10bar_1h.loc[ot]) else None
                    d_val = float(dist_ema150_4h_pct.loc[ot]) if pd.notna(dist_ema150_4h_pct.loc[ot]) else None
                    self._record_l2_rejection(
                        signal_id=sig_id,
                        side=side,
                        signal_close_ms=close_ms,
                        reason=h1_reason,
                        slope_val=s_val,
                        dist_val=d_val,
                    )

        ep = self.get_exit_policy(eth_c1.symbol, eth_c1.combo_id)
        ep_hash = ep.get_policy_hash()
        ob_col = "buy_blocked" if side == "BUY" else "sell_blocked"

        out: list[Signal] = []
        for ot in ev.index[ev.fillna(False)]:
            last = ot + pd.Timedelta(minutes=45)
            if last not in posmap:
                continue
            pos = posmap[last]
            warmup_ok = pos >= (warmup_bars - 1)
            sd = int(smc1.loc[ot])
            blocked = bool(ob1.loc[ot][ob_col])

            s_val = float(slope_10bar_1h.loc[ot]) if pd.notna(slope_10bar_1h.loc[ot]) else None
            d_val = float(dist_ema150_4h_pct.loc[ot]) if pd.notna(dist_ema150_4h_pct.loc[ot]) else None

            if warmup_ok:
                dec = evaluate_eth_c1_l2(
                    side,
                    sd,
                    blocked,
                    eth_c1.layer2,
                    slope_10bar_pct=s_val,
                    dist_ema150_4h_pct=d_val,
                    enable_h1=self.enable_eth_c1_h1_l2,
                    buy_max_slope_4h_pct=self.buy_max_slope_4h_pct,
                    sell_min_dist_ema150_4h_pct=self.sell_min_dist_ema150_4h_pct,
                )
            else:
                side_code = "L" if side == "BUY" else "S"
                smc_ok = bool(approved(side_code, sd, blocked, eth_c1.layer2))
                dec = L2Decision(approved=smc_ok, reason=None if smc_ok else eth_c1.layer2)

            close_ms = int(d15.index[pos].value // 1_000_000 + M15_MS - 1)
            sig_id = f"SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_{side}_{close_ms}"
            if not dec.approved and warmup_ok and dec.reason is not None:
                self._record_l2_rejection(
                    signal_id=sig_id,
                    side=side,
                    signal_close_ms=close_ms,
                    reason=dec.reason,
                    slope_val=s_val,
                    dist_val=d_val,
                )

            if only_approved and not dec.approved:
                continue
            if only_warmup_ready and not warmup_ok:
                continue

            bar_open_ms = int(d15.index[pos].value // 1_000_000)
            ref_price = float(d15.iloc[pos]["close"])
            provider = str(d15.iloc[pos].get("provider", "UNKNOWN"))
            tp_px = ref_price * (1.0 + ep.tp_pct) if side == "BUY" else ref_price * (1.0 - ep.tp_pct)
            sl_px = ref_price * (1.0 - ep.sl_pct) if side == "BUY" else ref_price * (1.0 + ep.sl_pct)

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
                    side=side,
                    entry_price=ref_price,
                    tp_price=tp_px,
                    sl_price=sl_px,
                    tp_pct=ep.tp_pct,
                    sl_pct=ep.sl_pct,
                    approved=dec.approved,
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
        """Generate signals across symbols, applying H1_BOTH_1 L2 exclusively to ETH-USDT/C1."""
        self.last_l2_rejections = []
        base_sigs = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        target_symbols = symbols or list(data.keys())
        if "ETH-USDT" not in target_symbols or "ETH-USDT" not in data:
            return base_sigs

        eth_features = compute_eth_c1_l2_features(data["ETH-USDT"])
        kept_non_c1 = [
            s for s in base_sigs
            if not (s.symbol == "ETH-USDT" and s.combo_id == 1)
        ]

        buys = self._generate_eth_c1_side(
            eth_features,
            side="BUY",
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        sells = self._generate_eth_c1_side(
            eth_features,
            side="SELL",
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        combined = kept_non_c1 + buys + sells
        combined.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return combined
