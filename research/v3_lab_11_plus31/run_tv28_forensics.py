"""Read-only diagnostic & forensics audit for TV28 (BTC-USDT / D1) in PLUS43."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_backtest as core
import run_plus43_forward as p43_fwd
import run_plus44 as plus44
import run_plus44_all_signals as p44_all
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.sim.engine import UnifiedSimulator

OUT_DIR = ROOT / "reports" / "tv28_forensics"
TV20_KEPT: set[str] = set(plus44.KEPT) - {"TV13"}

BASELINE_TV28 = {
    "entries": 41,
    "net_pnl": 4.86355496,
    "fees": 4.09640904,
    "slippage": 1.63856403,
    "funding": -0.178524,
}
BASELINE_FULL_BASE = {
    "executed_trades": 6472,
    "net_pnl": 1525.8432924460908,
    "m2m_dd": 176.5862,
    "peak_concurrent_legs": 37,
}


def _signed_funding_cost(
    cost_model: Any,
    symbol: str,
    side: str,
    notional: float,
    entry_time_ms: int,
    exit_time_ms: int,
) -> float:
    """Compute signed funding cost matching SignedFundingCostModel.evaluate_leg."""
    raw = float(
        cost_model.compute_funding_cost(
            symbol=symbol,
            notional=float(notional),
            entry_time_ms=int(entry_time_ms),
            exit_time_ms=int(exit_time_ms),
        )
    )
    return raw if str(side).upper() in ("BUY", "LONG", "L") else -raw


def _split_label(entry_ms: int) -> str:
    short_map = {"DEVELOPMENT": "DEV", "VALIDATION": "VAL", "HOLDOUT": "HOLDOUT"}
    for sp in p44_all.V3_18_SPLITS:
        if int(sp["start_ms"]) <= entry_ms <= int(sp["end_ms"]):
            return short_map[sp["split"]]
    if entry_ms >= p43_fwd.FORWARD_START_MS:
        return "FORWARD"
    return "UNKNOWN"


def _year_label(entry_ms: int) -> str:
    for yp in p44_all.YEARLY_PERIODS:
        if int(yp["start_ms"]) <= entry_ms < int(yp["end_ms_exclusive"]):
            return str(yp["period"])
    if entry_ms >= p43_fwd.FORWARD_START_MS:
        return "FORWARD"
    return "UNKNOWN"


def _side_label(side: str) -> str:
    return "LONG" if str(side).upper() in ("BUY", "LONG", "L") else "SHORT"


def _ms_to_iso(ms: int) -> str:
    return pd.to_datetime(int(ms), unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_d1_regime_indicators(d1_bars: pd.DataFrame) -> pd.DataFrame:
    """Compute causal Wilder ATR(14)/Close (%) and ADX(14) on closed D1 bars."""
    df = d1_bars.sort_index().copy()
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
        index=df.index,
    )

    alpha = 1.0 / 14.0
    atr14 = tr.ewm(alpha=alpha, adjust=False, min_periods=14).mean()
    plus_sm = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=14).mean()
    minus_sm = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=14).mean()

    plus_di = 100.0 * (plus_sm / atr14.replace(0.0, np.nan))
    minus_di = 100.0 * (minus_sm / atr14.replace(0.0, np.nan))
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx14 = dx.ewm(alpha=alpha, adjust=False, min_periods=14).mean()

    out = pd.DataFrame(
        {
            "bar_open_ms": (df.index.asi8 // 1_000_000).astype(np.int64),
            "adx14": adx14.to_numpy(dtype=float),
            "atr14": atr14.to_numpy(dtype=float),
            "atr_pct": (atr14 / close * 100.0).to_numpy(dtype=float),
        },
        index=df.index,
    )
    return out


def _compute_mae_mfe(
    m1_t_ms: np.ndarray,
    m1_highs: np.ndarray,
    m1_lows: np.ndarray,
    entry_ms: int,
    exit_ms: int,
    side: str,
    entry_px: float,
    sl_pct: float,
) -> tuple[float, float, float, float]:
    """Compute MAE/MFE (% and R) using M1 High/Low across [entry_ms, exit_ms]."""
    i0 = int(np.searchsorted(m1_t_ms, entry_ms, side="left"))
    i1 = int(np.searchsorted(m1_t_ms, exit_ms, side="right"))
    if i1 <= i0:
        return 0.0, 0.0, 0.0, 0.0
    max_high = float(np.max(m1_highs[i0:i1]))
    min_low = float(np.min(m1_lows[i0:i1]))
    if _side_label(side) == "LONG":
        mae_pct = max(0.0, (entry_px - min_low) / entry_px * 100.0)
        mfe_pct = max(0.0, (max_high - entry_px) / entry_px * 100.0)
    else:
        mae_pct = max(0.0, (max_high - entry_px) / entry_px * 100.0)
        mfe_pct = max(0.0, (entry_px - min_low) / entry_px * 100.0)
    r_pct = float(sl_pct) * 100.0
    mae_r = mae_pct / r_pct if r_pct > 0 else 0.0
    mfe_r = mfe_pct / r_pct if r_pct > 0 else 0.0
    return mae_pct, mfe_pct, mae_r, mfe_r


def _segment_stats(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "entries": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "net_pnl": 0.0,
            "net_pf": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": None,
        }
    pnl = df["net_pnl"].astype(float)
    wins = pnl[pnl > 0.0]
    losses = pnl[pnl < 0.0]
    win_sum = float(wins.sum())
    loss_abs = float(-losses.sum())
    avg_win = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = float(losses.mean()) if not losses.empty else 0.0
    payoff = float(avg_win / abs(avg_loss)) if (not wins.empty and not losses.empty and abs(avg_loss) > 0) else None
    pf = float(win_sum / loss_abs) if loss_abs > 0 else (999.0 if win_sum > 0 else 0.0)
    return {
        "entries": int(len(df)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_pct": float((pnl > 0.0).mean() * 100.0),
        "net_pnl": float(pnl.sum()),
        "net_pf": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
    }


def _side_split_block(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "ALL": _segment_stats(df),
        "LONG": _segment_stats(df.loc[df["side"] == "LONG"]),
        "SHORT": _segment_stats(df.loc[df["side"] == "SHORT"]),
    }


def _run_tv28_custom_slippage(
    tv28_signals: list[Any],
    m1_btc: pd.DataFrame,
    slippage_bps: float,
    funding_tbl: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame]:
    core.configure_tv_lab()
    model = core.tvlab.SignedFundingCostModel.from_uniform_params(
        name=f"CUSTOM_SLIP_{slippage_bps:.4f}",
        fee_taker=0.0005,
        slippage_bps=float(slippage_bps),
    )
    model.funding_table = funding_tbl
    risk = RiskConfig(
        initial_equity=core.INITIAL_EQUITY,
        notional_per_trade=core.NOTIONAL,
        leverage_btc=100,
        leverage_eth=50,
        cost_profile=model.profile.name,
        cost_model=model,
    )
    res = UnifiedSimulator(
        risk_config=risk,
        final14_exit_mode="100_HARD",
        cost_model=model,
        ambiguity_policy="CONSERVATIVE",
    ).run("ALL_SIGNALS_PORTFOLIO", tv28_signals, {"BTC-USDT": m1_btc}, sample_snapshot_interval_m1=15)
    _, raw_frame = core.result_payload(res)
    frame = core.normalized_trades(raw_frame, "TV28_STANDALONE", model.profile.name)
    snaps = res.snapshots.copy()
    ordered = frame.sort_values(["exit_time_ms", "entry_time_ms", "trade_id"]) if not frame.empty else frame
    own = ordered["pnl_own_entry"].astype(float) if not ordered.empty else pd.Series([], dtype=float)
    vwap = ordered["net_pnl"].astype(float) if not ordered.empty else pd.Series([], dtype=float)
    m2m_usd, m2m_pct, _ = p44_all._compute_snapshot_dd(snaps)
    closed_dd = p44_all._compute_closed_dd(own, core.INITIAL_EQUITY)
    funding_sum = (
        float(
            sum(
                _signed_funding_cost(
                    model,
                    str(r.symbol),
                    str(r.side),
                    float(r.notional),
                    int(r.entry_time_ms),
                    int(r.exit_time_ms),
                )
                for r in ordered.itertuples()
            )
        )
        if not ordered.empty
        else 0.0
    )
    stats = {
        "slippage_bps": float(slippage_bps),
        "entries": int(len(ordered)),
        "net_pnl": float(own.sum()),
        "net_pnl_vwap": float(vwap.sum()),
        "net_pf": p44_all._calc_profit_factor(own),
        "win_rate_pct": float((own > 0.0).mean() * 100.0) if not own.empty else 0.0,
        "max_dd_closed": float(closed_dd),
        "m2m_max_dd_dollars": float(m2m_usd),
        "m2m_max_dd_pct": float(m2m_pct),
        "gross_pnl_raw": float(ordered["gross_pnl_raw"].astype(float).sum()) if not ordered.empty else 0.0,
        "fees": float(ordered["fee_total"].astype(float).sum()) if not ordered.empty else 0.0,
        "slippage": float(ordered["slippage_cost"].astype(float).sum()) if not ordered.empty else 0.0,
        "funding": funding_sum,
        "tp_count": int(ordered["exit_reason"].eq("TP").sum()) if not ordered.empty else 0,
        "sl_count": int(ordered["exit_reason"].eq("SL").sum()) if not ordered.empty else 0,
        "force_close_count": int(ordered["exit_reason"].eq("FORCE_CLOSE").sum()) if not ordered.empty else 0,
    }
    return stats, ordered


def _summarize_portfolio_window(
    frame: pd.DataFrame,
    snaps: pd.DataFrame,
    start_ms: int | None = None,
    end_ms: int | None = None,
    *,
    is_full_window: bool = False,
) -> dict[str, Any]:
    if start_ms is not None and end_ms is not None:
        t_sub = frame.loc[(frame["entry_time_ms"] >= start_ms) & (frame["entry_time_ms"] <= end_ms)]
        s_sub = snaps.loc[(snaps["timestamp_ms"] >= start_ms) & (snaps["timestamp_ms"] <= end_ms)] if not snaps.empty else snaps
    else:
        t_sub = frame
        s_sub = snaps

    ordered = t_sub.sort_values(["exit_time_ms", "entry_time_ms", "trade_id"]) if not t_sub.empty else t_sub
    pnl = ordered["net_pnl"].astype(float) if not ordered.empty else pd.Series([], dtype=float)
    own_pnl = ordered["pnl_own_entry"].astype(float) if not ordered.empty else pd.Series([], dtype=float)
    m2m_local, m2m_local_pct, m2m_global = p44_all._compute_snapshot_dd(s_sub)
    if is_full_window:
        m2m_usd = float(m2m_global)
        m2m_pct = float(s_sub["drawdown_pct"].astype(float).max()) if not s_sub.empty and "drawdown_pct" in s_sub else float(m2m_local_pct)
    else:
        m2m_usd = float(m2m_local)
        m2m_pct = float(m2m_local_pct)

    return {
        "entries": int(len(ordered)),
        "net_pnl": float(pnl.sum()),
        "net_pnl_own_entry": float(own_pnl.sum()),
        "gross_pnl": float(ordered["gross_pnl"].astype(float).sum()) if not ordered.empty else 0.0,
        "fees": float(ordered["fee_total"].astype(float).sum()) if not ordered.empty else 0.0,
        "slippage": float(ordered["slippage_cost"].astype(float).sum()) if not ordered.empty else 0.0,
        "funding": float(ordered["funding_cost"].astype(float).sum()) if not ordered.empty else 0.0,
        "m2m_max_dd_dollars": m2m_usd,
        "m2m_max_dd_pct": m2m_pct,
        "closed_max_dd_dollars": float(p44_all._compute_closed_dd(pnl, core.INITIAL_EQUITY)),
        "profit_factor": float(p44_all._calc_profit_factor(pnl)),
        "profit_factor_own_entry": float(p44_all._calc_profit_factor(own_pnl)),
        "win_rate_pct": float((pnl > 0.0).mean() * 100.0) if not pnl.empty else 0.0,
        "peak_concurrent_legs": int(plus44.peak_concurrent(ordered)) if not ordered.empty else 0,
    }


def _safe_pearson(a: pd.Series, b: pd.Series) -> float | None:
    if len(a) < 2 or float(a.std(ddof=0)) == 0.0 or float(b.std(ddof=0)) == 0.0:
        return None
    val = float(a.corr(b))
    return None if np.isnan(val) else val


def _run_forward_tv28(tv28_row: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    data_root = ROOT / "data" / "bingx"
    m1_fwd_dict, max_m1_ms = p43_fwd._load_combined_m1(data_root)
    funding_tbl = p43_fwd._load_forward_funding(data_root)
    m1_btc = m1_fwd_dict["BTC-USDT"]

    bars = p43_fwd._resample_closed_safe(m1_btc, int(tv28_row["timeframe_minutes"]))
    evaluated = core.tvlab.PORT_CLASSES[str(tv28_row["strategy_id"])]().evaluate(bars, str(tv28_row["variant"]))
    warmup = int(tv28_row["warmup_bars"])
    if warmup and len(evaluated) > warmup:
        evaluated.loc[evaluated.index[:warmup], "signal"] = 0
    evaluated = evaluated.join(bars[["open", "high", "low", "close", "volume", "provider"]])
    transformed = core.tvlab.apply_variant(evaluated, str(tv28_row["variant_id"]))
    sigs = core.tvlab.make_signals(
        transformed,
        tv28_row,
        float(tv28_row["tp_pct"]),
        float(tv28_row["sl_pct"]),
        str(tv28_row["variant_id"]),
    )
    numeric_id = 31000 + int(str(tv28_row["display_id"])[2:])
    fwd_sigs = [
        replace(
            s,
            combo_id=numeric_id,
            combo_name="TV28",
            signal_id=(
                f"{tv28_row['display_id']}|{tv28_row['run_id']}|{tv28_row['strategy_source_sha256']}|"
                f"{s.signal_close_ms}|{s.side}"
            ),
        )
        for s in sigs
        if int(s.signal_close_ms) + 1 >= p43_fwd.FORWARD_START_MS
    ]

    end_utc = _ms_to_iso(max_m1_ms)
    fwd_meta: dict[str, Any] = {
        "start_utc": p43_fwd.FORWARD_START_ISO,
        "end_utc": end_utc,
        "tv28_signals": len(fwd_sigs),
        "tv28_executed_trades": 0,
        "tv28_net_pnl": 0.0,
        "m1_btc_combined_df": m1_btc,
        "fwd_funding_tbl": funding_tbl,
    }
    if not fwd_sigs:
        return pd.DataFrame(), fwd_meta

    sim_start_ts = pd.to_datetime(p43_fwd.SIM_M1_START_MS, unit="ms", utc=True)
    m1_sim = {"BTC-USDT": m1_btc.loc[m1_btc.index >= sim_start_ts]}
    model = core.tvlab.SignedFundingCostModel.from_profile_name(
        "BASE", is_real_data=True, funding_table=funding_tbl
    )
    risk = RiskConfig(
        initial_equity=core.INITIAL_EQUITY,
        notional_per_trade=core.NOTIONAL,
        leverage_btc=100,
        leverage_eth=50,
        cost_profile="BASE",
        cost_model=model,
    )
    res = UnifiedSimulator(
        risk_config=risk,
        final14_exit_mode="100_HARD",
        cost_model=model,
        ambiguity_policy="CONSERVATIVE",
    ).run("ALL_SIGNALS_PORTFOLIO", fwd_sigs, m1_sim, sample_snapshot_interval_m1=15)
    _, raw_frame = core.result_payload(res)
    frame = core.normalized_trades(raw_frame, "TV28_FORWARD", "BASE")
    fwd_meta["tv28_executed_trades"] = int(len(frame))
    fwd_meta["tv28_net_pnl"] = float(frame["pnl_own_entry"].astype(float).sum()) if not frame.empty else 0.0
    return frame, fwd_meta


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    core.configure_tv_lab()
    core.ensure_fixed_v1_registered()
    p44_all._validate_splits_against_report()

    integrity = core.DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(f"Data integrity check failed: {integrity}")

    full_registry = core.load_registry()
    registry = (
        full_registry.loc[full_registry.display_id.isin(TV20_KEPT)]
        .sort_values("display_id")
        .reset_index(drop=True)
    )
    assert len(registry) == 20 and registry.display_id.nunique() == 20

    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    print(f"[1/6] Generating 20 TV signal sets with {workers} workers...", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(core._tv_worker, row): row["display_id"]
            for row in registry.to_dict("records")
        }
        for future in as_completed(futures):
            payload = future.result()
            tv_signals.extend(payload["signals"])

    layer = core.DataLayer()
    m1 = {sym: layer.query_m1(sym, core.START, core.END) for sym in ("BTC-USDT", "ETH-USDT")}
    start_ms = int(pd.Timestamp(core.START, tz="UTC").value // 10**6)
    end_ms = int(pd.Timestamp(core.END, tz="UTC").value // 10**6)
    m15 = {
        sym: layer.query_timeframe(sym, "15m", core.START, core.END, include_warmup=True)
        for sym in m1
    }
    prune = core.V3Lab11Prune23Strategy()
    prune_signals = [
        sig
        for sig in prune.generate_signals(m15, symbols=list(m1), only_approved=True, only_warmup_ready=True)
        if start_ms <= int(sig.signal_close_ms) + 1 < end_ms
    ]
    assert len(prune_signals) == 4247 and len(tv_signals) == 2225
    all_signals = prune_signals + tv_signals

    # Full BASE simulation
    print("[2/6] Running FULL PRUNE23_PLUS_TV20 BASE portfolio simulation...", flush=True)
    full_res = p44_all.run_all_signals_portfolio(all_signals, m1, "BASE", with_funding=True)
    full_metrics, full_raw = core.result_payload(full_res)
    full_trades = core.normalized_trades(full_raw, "PRUNE23_PLUS_TV20", "BASE")
    full_snaps = full_res.snapshots.copy()
    full_peak_legs = plus44.peak_concurrent(full_trades)

    tv28_3y = full_trades.loc[full_trades["display_id"] == "TV28"].sort_values(
        ["entry_time_ms", "trade_id"]
    ).copy()
    tv27_3y = full_trades.loc[full_trades["display_id"] == "TV27"].sort_values(
        ["entry_time_ms", "trade_id"]
    ).copy()

    funding_tbl = core.tvlab.funding_table()
    base_cost_model = core.tvlab.SignedFundingCostModel.from_profile_name(
        "BASE", is_real_data=True, funding_table=funding_tbl
    )
    tv28_funding_3y = float(
        sum(
            _signed_funding_cost(
                base_cost_model,
                str(r.symbol),
                str(r.side),
                float(r.notional),
                int(r.entry_time_ms),
                int(r.exit_time_ms),
            )
            for r in tv28_3y.itertuples()
        )
    )
    full_own_sum = float(full_trades["pnl_own_entry"].astype(float).sum())

    # Baseline reconciliation
    recon = {
        "tv28_entries": {
            "expected": BASELINE_TV28["entries"],
            "actual": int(len(tv28_3y)),
            "diff": int(len(tv28_3y) - BASELINE_TV28["entries"]),
            "pass": len(tv28_3y) == BASELINE_TV28["entries"],
        },
        "tv28_net_pnl_own_entry": {
            "expected": BASELINE_TV28["net_pnl"],
            "actual": float(tv28_3y["pnl_own_entry"].astype(float).sum()),
            "diff": float(tv28_3y["pnl_own_entry"].astype(float).sum() - BASELINE_TV28["net_pnl"]),
            "pass": abs(float(tv28_3y["pnl_own_entry"].astype(float).sum()) - BASELINE_TV28["net_pnl"]) < 1e-6,
        },
        "tv28_fees": {
            "expected": BASELINE_TV28["fees"],
            "actual": float(tv28_3y["fee_total"].astype(float).sum()),
            "diff": float(tv28_3y["fee_total"].astype(float).sum() - BASELINE_TV28["fees"]),
            "pass": abs(float(tv28_3y["fee_total"].astype(float).sum()) - BASELINE_TV28["fees"]) < 1e-6,
        },
        "tv28_slippage": {
            "expected": BASELINE_TV28["slippage"],
            "actual": float(tv28_3y["slippage_cost"].astype(float).sum()),
            "diff": float(tv28_3y["slippage_cost"].astype(float).sum() - BASELINE_TV28["slippage"]),
            "pass": abs(float(tv28_3y["slippage_cost"].astype(float).sum()) - BASELINE_TV28["slippage"]) < 1e-6,
        },
        "tv28_funding": {
            "expected": BASELINE_TV28["funding"],
            "actual": tv28_funding_3y,
            "diff": tv28_funding_3y - BASELINE_TV28["funding"],
            "pass": abs(tv28_funding_3y - BASELINE_TV28["funding"]) < 1e-6,
        },
        "portfolio_executed_trades": {
            "expected": BASELINE_FULL_BASE["executed_trades"],
            "actual": int(full_metrics["executed_trades"]),
            "diff": int(full_metrics["executed_trades"] - BASELINE_FULL_BASE["executed_trades"]),
            "pass": int(full_metrics["executed_trades"]) == BASELINE_FULL_BASE["executed_trades"],
        },
        "portfolio_net_pnl_own_entry": {
            "expected": BASELINE_FULL_BASE["net_pnl"],
            "actual": full_own_sum,
            "diff": full_own_sum - BASELINE_FULL_BASE["net_pnl"],
            "pass": abs(full_own_sum - BASELINE_FULL_BASE["net_pnl"]) < 1e-4,
        },
        "portfolio_peak_concurrent_legs": {
            "expected": BASELINE_FULL_BASE["peak_concurrent_legs"],
            "actual": int(full_peak_legs),
            "diff": int(full_peak_legs - BASELINE_FULL_BASE["peak_concurrent_legs"]),
            "pass": int(full_peak_legs) == BASELINE_FULL_BASE["peak_concurrent_legs"],
        },
    }
    engine_refactor_info = {
        "manifest_git_head": "4703ae2996587b4282bb50dfc70c49a57f8810c3",
        "current_head_portfolio_net_pnl_vwap": float(full_metrics["net_pnl"]),
        "manifest_portfolio_net_pnl_vwap": BASELINE_FULL_BASE["net_pnl"],
        "vwap_diff_due_to_1a5e77e_funding_refactor": float(full_metrics["net_pnl"] - BASELINE_FULL_BASE["net_pnl"]),
        "current_head_portfolio_m2m_dd": float(full_metrics["m2m_dd"]),
        "manifest_portfolio_m2m_dd": BASELINE_FULL_BASE["m2m_dd"],
        "note": (
            "In commit 1a5e77e, engine.py moved portfolio-level funding from cb.funding_cost to "
            "UnifiedSimulator(..., funding_table=FundingTable) while run_plus44_all_signals.py passes "
            "funding_table=None. Thus on HEAD, pnl_own_entry (cb.net_pnl) matches 4703ae2 100% "
            "(1525.843292 portfolio / 4.863555 TV28), whereas TradeRecord.net_pnl (VWAP) excludes "
            "cb.funding_cost (3.575341) and M2M DD is 178.965 vs 176.5862."
        ),
    }
    all_recon_pass = all(v["pass"] for v in recon.values())
    print(f"    Baseline reconciliation: {'PASS' if all_recon_pass else 'FAIL'}", flush=True)
    if not all_recon_pass:
        raise RuntimeError(f"Baseline reconciliation failed: {json.dumps(recon, indent=2)}")

    # Forward check + combined BTC M1 for indicators and MAE/MFE
    print("[3/6] Checking Forward window and computing Test 1 (Trade Detail + MAE/MFE)...", flush=True)
    tv28_row = registry.loc[registry["display_id"] == "TV28"].iloc[0].to_dict()
    tv28_fwd_trades, fwd_meta = _run_forward_tv28(tv28_row)
    m1_btc_combined_tail = fwd_meta.pop("m1_btc_combined_df")
    fwd_funding_tbl = fwd_meta.pop("fwd_funding_tbl")
    fwd_cost_model = core.tvlab.SignedFundingCostModel.from_profile_name(
        "BASE", is_real_data=True, funding_table=fwd_funding_tbl
    )
    m1_btc_all = pd.concat([m1["BTC-USDT"], m1_btc_combined_tail])
    m1_btc_all = m1_btc_all[~m1_btc_all.index.duplicated(keep="last")].sort_index()

    # D1 closed-bar indicators (trimming any incomplete trailing D1 bar)
    d1_bars = p43_fwd._resample_closed_safe(m1_btc_all, 1440)
    d1_ind = _compute_d1_regime_indicators(d1_bars)
    d1_open_ms = d1_ind["bar_open_ms"].to_numpy(dtype=np.int64)
    d1_adx = d1_ind["adx14"].to_numpy(dtype=float)
    d1_atr_pct = d1_ind["atr_pct"].to_numpy(dtype=float)

    m1_t_ms = (m1_btc_all.index.asi8 // 1_000_000).astype(np.int64)
    m1_highs = m1_btc_all["high"].to_numpy(dtype=float)
    m1_lows = m1_btc_all["low"].to_numpy(dtype=float)

    combined_tv28_trades = pd.concat([tv28_3y, tv28_fwd_trades], ignore_index=True) if not tv28_fwd_trades.empty else tv28_3y.copy()
    detail_rows: list[dict[str, Any]] = []
    for row in combined_tv28_trades.sort_values(["entry_time_ms", "trade_id"]).itertuples():
        e_ms = int(row.entry_time_ms)
        x_ms = int(row.exit_time_ms)
        side_lbl = _side_label(row.side)
        entry_px = float(row.entry_px_raw)
        sl_pct = float(row.sl_pct)

        mae_pct, mfe_pct, mae_r, mfe_r = _compute_mae_mfe(
            m1_t_ms, m1_highs, m1_lows, e_ms, x_ms, row.side, entry_px, sl_pct
        )

        # Causal signal bar: last closed D1 bar whose close <= entry_time_ms (open <= entry_time_ms - 86_400_000)
        sig_idx = int(np.searchsorted(d1_open_ms, e_ms - 86_400_000, side="right")) - 1
        adx_val = float(d1_adx[sig_idx]) if sig_idx >= 0 and not np.isnan(d1_adx[sig_idx]) else np.nan
        atr_val = float(d1_atr_pct[sig_idx]) if sig_idx >= 0 and not np.isnan(d1_atr_pct[sig_idx]) else np.nan

        sp_lbl = _split_label(e_ms)
        cm = fwd_cost_model if sp_lbl == "FORWARD" else base_cost_model
        funding_val = _signed_funding_cost(
            cm, str(row.symbol), str(row.side), float(row.notional), e_ms, x_ms
        )

        detail_rows.append(
            {
                "trade_id": str(row.trade_id),
                "split": sp_lbl,
                "year": _year_label(e_ms),
                "side": side_lbl,
                "entry_time_utc": _ms_to_iso(e_ms),
                "exit_time_utc": _ms_to_iso(x_ms),
                "holding_bars": round((x_ms - e_ms) / 86_400_000.0, 6),
                "entry_price": entry_px,
                "exit_price": float(row.exit_px_fill),
                "sl_price": float(row.sl_price),
                "tp_price": float(row.tp_price),
                "exit_reason": str(row.exit_reason),
                "gross_pnl": float(row.gross_pnl_raw),
                "fees": float(row.fee_total),
                "slippage": float(row.slippage_cost),
                "funding": funding_val,
                "net_pnl": float(row.pnl_own_entry),
                "mae_pct": mae_pct,
                "mfe_pct": mfe_pct,
                "mae_r": mae_r,
                "mfe_r": mfe_r,
                "entry_adx14": adx_val,
                "entry_atr_pct": atr_val,
            }
        )

    detail_df = pd.DataFrame(detail_rows)
    detail_3y_chk = detail_df.loc[detail_df["split"] != "FORWARD"]
    assert abs(float(detail_3y_chk["funding"].sum()) - BASELINE_TV28["funding"]) < 1e-6
    acct_err = (
        detail_df["gross_pnl"] - detail_df["fees"] - detail_df["slippage"] - detail_df["funding"] - detail_df["net_pnl"]
    ).abs().max()
    assert float(acct_err) < 1e-4, f"Per-trade accounting mismatch: max_err={acct_err}"
    detail_csv_path = OUT_DIR / "tv28_trades_detail.csv"
    detail_df.to_csv(detail_csv_path, index=False)

    # Test 2: Breakdown (3Y trades)
    print("[4/6] Computing Test 2 (Long/Short & Regime Breakdown)...", flush=True)
    detail_3y = detail_df.loc[detail_df["split"] != "FORWARD"].copy()
    detail_fwd = detail_df.loc[detail_df["split"] == "FORWARD"].copy()

    by_exit_reason: dict[str, Any] = {}
    for reason, part in detail_3y.groupby("exit_reason", sort=True):
        st = _segment_stats(part)
        st["share_pct"] = float(len(part) / len(detail_3y) * 100.0)
        st["avg_pnl"] = float(part["net_pnl"].mean())
        st["avg_holding_bars"] = float(part["holding_bars"].mean())
        by_exit_reason[str(reason)] = st

    adx_bins = [
        ("ADX_LT_20", detail_3y["entry_adx14"] < 20.0),
        ("ADX_20_TO_30", (detail_3y["entry_adx14"] >= 20.0) & (detail_3y["entry_adx14"] < 30.0)),
        ("ADX_GE_30", detail_3y["entry_adx14"] >= 30.0),
    ]
    atr_bins = [
        ("ATR_PCT_LT_2.5", detail_3y["entry_atr_pct"] < 2.5),
        ("ATR_PCT_2.5_TO_4.0", (detail_3y["entry_atr_pct"] >= 2.5) & (detail_3y["entry_atr_pct"] < 4.0)),
        ("ATR_PCT_GE_4.0", detail_3y["entry_atr_pct"] >= 4.0),
    ]
    breakdown_payload = {
        "strategy": "TV28",
        "symbol": "BTC-USDT",
        "timeframe": "D1",
        "basis": "OWN_ENTRY_ONLY",
        "window_3y": {"start": core.START, "end_exclusive": core.END, "entries": int(len(detail_3y))},
        "overall": _side_split_block(detail_3y),
        "by_split": {
            sp: _side_split_block(detail_3y.loc[detail_3y["split"] == sp])
            for sp in ("DEV", "VAL", "HOLDOUT")
        },
        "by_year": {
            yr: _side_split_block(detail_3y.loc[detail_3y["year"] == yr])
            for yr in ("Y1", "Y2", "Y3")
        },
        "by_exit_reason": by_exit_reason,
        "by_regime": {
            "adx14": {lbl: _side_split_block(detail_3y.loc[mask]) for lbl, mask in adx_bins},
            "atr_pct": {lbl: _side_split_block(detail_3y.loc[mask]) for lbl, mask in atr_bins},
        },
        "mae_mfe_summary": {
            "all": {
                "avg_mae_pct": float(detail_3y["mae_pct"].mean()),
                "median_mae_pct": float(detail_3y["mae_pct"].median()),
                "avg_mfe_pct": float(detail_3y["mfe_pct"].mean()),
                "median_mfe_pct": float(detail_3y["mfe_pct"].median()),
                "avg_mae_r": float(detail_3y["mae_r"].mean()),
                "avg_mfe_r": float(detail_3y["mfe_r"].mean()),
            },
            "winners": {
                "count": int((detail_3y["net_pnl"] > 0).sum()),
                "avg_mae_r": float(detail_3y.loc[detail_3y["net_pnl"] > 0, "mae_r"].mean()),
                "avg_mfe_r": float(detail_3y.loc[detail_3y["net_pnl"] > 0, "mfe_r"].mean()),
            },
            "losers": {
                "count": int((detail_3y["net_pnl"] <= 0).sum()),
                "avg_mae_r": float(detail_3y.loc[detail_3y["net_pnl"] <= 0, "mae_r"].mean()),
                "avg_mfe_r": float(detail_3y.loc[detail_3y["net_pnl"] <= 0, "mfe_r"].mean()),
                "losers_reached_1r_mfe_count": int(((detail_3y["net_pnl"] <= 0) & (detail_3y["mfe_r"] >= 1.0)).sum()),
                "losers_reached_1_5r_mfe_count": int(((detail_3y["net_pnl"] <= 0) & (detail_3y["mfe_r"] >= 1.5)).sum()),
                "losers_reached_2r_mfe_count": int(((detail_3y["net_pnl"] <= 0) & (detail_3y["mfe_r"] >= 2.0)).sum()),
            },
        },
        "forward": {
            **fwd_meta,
            "stats": _side_split_block(detail_fwd),
        },
    }
    (OUT_DIR / "tv28_breakdown.json").write_text(
        json.dumps(breakdown_payload, indent=2), encoding="utf-8"
    )

    # Test 3: Cost Sensitivity standalone
    print("[5/6] Running Test 3 (TV28 Standalone Cost Sensitivity & Breakeven)...", flush=True)
    tv28_signals = [s for s in tv_signals if s.combo_name == "TV28"]
    assert len(tv28_signals) == 41
    profile_bps = {"IDEAL": 0.0, "BASE": 2.0, "STRESS": 5.0}
    cost_profiles_out: dict[str, Any] = {}
    for prof, bps in profile_bps.items():
        st, _ = _run_tv28_custom_slippage(tv28_signals, m1["BTC-USDT"], bps, funding_tbl)
        cost_profiles_out[prof] = st

    # Exact bisection for breakeven slippage bps
    lo_bps = 0.0
    hi_bps = 25.0
    st_hi, _ = _run_tv28_custom_slippage(tv28_signals, m1["BTC-USDT"], hi_bps, funding_tbl)
    while st_hi["net_pnl"] > 0.0 and hi_bps < 200.0:
        hi_bps *= 2.0
        st_hi, _ = _run_tv28_custom_slippage(tv28_signals, m1["BTC-USDT"], hi_bps, funding_tbl)

    if cost_profiles_out["IDEAL"]["net_pnl"] <= 0.0:
        be_bps = 0.0
    elif st_hi["net_pnl"] > 0.0:
        be_bps = hi_bps
    else:
        for _ in range(18):
            mid = 0.5 * (lo_bps + hi_bps)
            st_mid, _ = _run_tv28_custom_slippage(tv28_signals, m1["BTC-USDT"], mid, funding_tbl)
            if st_mid["net_pnl"] > 0.0:
                lo_bps = mid
            else:
                hi_bps = mid
        be_bps = 0.5 * (lo_bps + hi_bps)
    be_check, _ = _run_tv28_custom_slippage(tv28_signals, m1["BTC-USDT"], be_bps, funding_tbl)
    ideal_net = cost_profiles_out["IDEAL"]["net_pnl"]
    stress_net = cost_profiles_out["STRESS"]["net_pnl"]
    linear_be = 5.0 * ideal_net / (ideal_net - stress_net) if (ideal_net - stress_net) > 0 else None

    cost_payload = {
        "strategy": "TV28",
        "mode": "STANDALONE_ALL_SIGNALS",
        "signals": len(tv28_signals),
        "profiles": cost_profiles_out,
        "breakeven_slippage_bps": round(float(be_bps), 4),
        "breakeven_net_pnl_at_bps": float(be_check["net_pnl"]),
        "breakeven_slippage_bps_linear_est": round(float(linear_be), 4) if linear_be is not None else None,
        "note": (
            "Standalone TV28 uses SignedFundingCostModel with fee_taker=0.0005 (5 bps/side) and "
            "uniform adverse slippage on entry_market, tp, stop, trailing_stop."
        ),
    }
    (OUT_DIR / "tv28_cost_sensitivity.json").write_text(
        json.dumps(cost_payload, indent=2), encoding="utf-8"
    )

    # Test 4: Portfolio Ablation
    print("[6/6] Running Test 4 (LOO_NO_TV28 Ablation) & Test 5 (TV28 vs TV27 Overlap)...", flush=True)
    no_tv28_signals = [s for s in all_signals if s.combo_name != "TV28"]
    assert len(no_tv28_signals) == len(all_signals) - len(tv28_signals)
    loo_res = p44_all.run_all_signals_portfolio(no_tv28_signals, m1, "BASE", with_funding=True)
    _, loo_raw = core.result_payload(loo_res)
    loo_trades = core.normalized_trades(loo_raw, "LOO_NO_TV28", "BASE")
    loo_snaps = loo_res.snapshots.copy()

    split_defs = [
        ("FULL_3Y", None, None, True),
        ("DEV", int(p44_all.V3_18_SPLITS[0]["start_ms"]), int(p44_all.V3_18_SPLITS[0]["end_ms"]), False),
        ("VAL", int(p44_all.V3_18_SPLITS[1]["start_ms"]), int(p44_all.V3_18_SPLITS[1]["end_ms"]), False),
        ("HOLDOUT", int(p44_all.V3_18_SPLITS[2]["start_ms"]), int(p44_all.V3_18_SPLITS[2]["end_ms"]), False),
    ]
    ablation_windows: dict[str, Any] = {}
    for wname, s_ms, e_ms, is_full in split_defs:
        full_w = _summarize_portfolio_window(full_trades, full_snaps, s_ms, e_ms, is_full_window=is_full)
        loo_w = _summarize_portfolio_window(loo_trades, loo_snaps, s_ms, e_ms, is_full_window=is_full)
        delta_w = {
            k: (full_w[k] - loo_w[k]) if isinstance(full_w[k], (int, float)) else None
            for k in full_w
        }
        ablation_windows[wname] = {
            "FULL_PRUNE23_PLUS_TV20": full_w,
            "LOO_NO_TV28": loo_w,
            "DELTA_WITH_TV28_MINUS_WITHOUT": delta_w,
        }

    ablation_payload = {
        "profile": "BASE",
        "mode": "ALL_SIGNALS_PORTFOLIO",
        "engine_refactor_note": engine_refactor_info,
        "windows": ablation_windows,
    }
    (OUT_DIR / "tv28_ablation.json").write_text(
        json.dumps(ablation_payload, indent=2), encoding="utf-8"
    )

    # Test 5: Correlation & Overlap vs TV27
    cal_days = pd.date_range("2023-11-01", "2026-10-31", freq="D", tz="UTC")

    def _pnl_series(df: pd.DataFrame, col: str, time_col: str) -> pd.Series:
        if df.empty:
            return pd.Series(0.0, index=cal_days)
        dts = pd.to_datetime(df[time_col].astype(np.int64), unit="ms", utc=True).dt.floor("D")
        grouped = df[col].astype(float).groupby(dts).sum()
        return grouped.reindex(cal_days, fill_value=0.0)

    port_ex_tv28 = full_trades.loc[full_trades["display_id"] != "TV28"]
    s_tv28_exit = _pnl_series(tv28_3y, "pnl_own_entry", "exit_time_ms")
    s_tv27_exit = _pnl_series(tv27_3y, "pnl_own_entry", "exit_time_ms")
    s_port_exit = _pnl_series(full_trades, "pnl_own_entry", "exit_time_ms")
    s_ex28_exit = _pnl_series(port_ex_tv28, "pnl_own_entry", "exit_time_ms")

    s_tv28_entry = _pnl_series(tv28_3y, "pnl_own_entry", "entry_time_ms")
    s_tv27_entry = _pnl_series(tv27_3y, "pnl_own_entry", "entry_time_ms")
    s_port_entry = _pnl_series(full_trades, "pnl_own_entry", "entry_time_ms")
    s_ex28_entry = _pnl_series(port_ex_tv28, "pnl_own_entry", "entry_time_ms")

    w_tv28_exit = s_tv28_exit.resample("W-SUN").sum()
    w_tv27_exit = s_tv27_exit.resample("W-SUN").sum()
    w_port_exit = s_port_exit.resample("W-SUN").sum()
    w_ex28_exit = s_ex28_exit.resample("W-SUN").sum()

    w_tv28_entry = s_tv28_entry.resample("W-SUN").sum()
    w_tv27_entry = s_tv27_entry.resample("W-SUN").sum()
    w_port_entry = s_port_entry.resample("W-SUN").sum()
    w_ex28_entry = s_ex28_entry.resample("W-SUN").sum()

    correlations = {
        "exit_date_aligned": {
            "daily": {
                "TV28_vs_TV27": _safe_pearson(s_tv28_exit, s_tv27_exit),
                "TV28_vs_PORTFOLIO_FULL": _safe_pearson(s_tv28_exit, s_port_exit),
                "TV28_vs_PORTFOLIO_EX_TV28": _safe_pearson(s_tv28_exit, s_ex28_exit),
            },
            "weekly": {
                "TV28_vs_TV27": _safe_pearson(w_tv28_exit, w_tv27_exit),
                "TV28_vs_PORTFOLIO_FULL": _safe_pearson(w_tv28_exit, w_port_exit),
                "TV28_vs_PORTFOLIO_EX_TV28": _safe_pearson(w_tv28_exit, w_ex28_exit),
            },
        },
        "entry_date_aligned": {
            "daily": {
                "TV28_vs_TV27": _safe_pearson(s_tv28_entry, s_tv27_entry),
                "TV28_vs_PORTFOLIO_FULL": _safe_pearson(s_tv28_entry, s_port_entry),
                "TV28_vs_PORTFOLIO_EX_TV28": _safe_pearson(s_tv28_entry, s_ex28_entry),
            },
            "weekly": {
                "TV28_vs_TV27": _safe_pearson(w_tv28_entry, w_tv27_entry),
                "TV28_vs_PORTFOLIO_FULL": _safe_pearson(w_tv28_entry, w_port_entry),
                "TV28_vs_PORTFOLIO_EX_TV28": _safe_pearson(w_tv28_entry, w_ex28_entry),
            },
        },
    }

    overlap_pairs: list[dict[str, Any]] = []
    buckets: dict[str, list[float]] = {
        "no_overlap": [],
        "same_side_only": [],
        "opposite_side_only": [],
        "both_sides": [],
    }
    any_overlap_pnls: list[float] = []
    any_same_pnls: list[float] = []
    any_opp_pnls: list[float] = []

    tv27_records = list(tv27_3y.itertuples())
    for r28 in tv28_3y.itertuples():
        e28, x28 = int(r28.entry_time_ms), int(r28.exit_time_ms)
        s28 = _side_label(r28.side)
        p28 = float(r28.pnl_own_entry)
        has_same = False
        has_opp = False
        for r27 in tv27_records:
            e27, x27 = int(r27.entry_time_ms), int(r27.exit_time_ms)
            ov_start = max(e28, e27)
            ov_end = min(x28, x27)
            if ov_start < ov_end:
                s27 = _side_label(r27.side)
                rel = "SAME_SIDE" if s28 == s27 else "OPPOSITE_SIDE"
                if s28 == s27:
                    has_same = True
                else:
                    has_opp = True
                overlap_pairs.append(
                    {
                        "tv28_trade_id": str(r28.trade_id),
                        "tv28_side": s28,
                        "tv28_entry_utc": _ms_to_iso(e28),
                        "tv28_exit_utc": _ms_to_iso(x28),
                        "tv28_net_pnl": p28,
                        "tv27_trade_id": str(r27.trade_id),
                        "tv27_side": s27,
                        "tv27_entry_utc": _ms_to_iso(e27),
                        "tv27_exit_utc": _ms_to_iso(x27),
                        "tv27_net_pnl": float(r27.pnl_own_entry),
                        "relation": rel,
                        "overlap_hours": round((ov_end - ov_start) / 3_600_000.0, 4),
                    }
                )
        if has_same and has_opp:
            buckets["both_sides"].append(p28)
        elif has_same:
            buckets["same_side_only"].append(p28)
        elif has_opp:
            buckets["opposite_side_only"].append(p28)
        else:
            buckets["no_overlap"].append(p28)

        if has_same or has_opp:
            any_overlap_pnls.append(p28)
        if has_same:
            any_same_pnls.append(p28)
        if has_opp:
            any_opp_pnls.append(p28)

    def _ov_summary(vals: list[float]) -> dict[str, Any]:
        return {
            "count": int(len(vals)),
            "share_pct": float(len(vals) / len(tv28_3y) * 100.0),
            "net_pnl": float(sum(vals)),
            "win_rate_pct": float(sum(1 for v in vals if v > 0.0) / len(vals) * 100.0) if vals else 0.0,
        }

    overlap_payload = {
        "tv28_entries": int(len(tv28_3y)),
        "tv27_entries": int(len(tv27_3y)),
        "tv28_net_pnl_own": float(tv28_3y["pnl_own_entry"].astype(float).sum()),
        "tv27_net_pnl_own": float(tv27_3y["pnl_own_entry"].astype(float).sum()),
        "correlations": correlations,
        "tv28_overlap_groups_exclusive": {k: _ov_summary(v) for k, v in buckets.items()},
        "tv28_overlap_groups_inclusive": {
            "any_overlap_with_tv27": _ov_summary(any_overlap_pnls),
            "overlaps_same_side_with_tv27": _ov_summary(any_same_pnls),
            "overlaps_opposite_side_with_tv27": _ov_summary(any_opp_pnls),
        },
        "overlap_pair_count": int(len(overlap_pairs)),
        "overlap_pairs": overlap_pairs,
    }
    (OUT_DIR / "tv28_vs_tv27_overlap.json").write_text(
        json.dumps(overlap_payload, indent=2), encoding="utf-8"
    )

    # Summary Markdown Report
    ov_all = breakdown_payload["overall"]["ALL"]
    ov_long = breakdown_payload["overall"]["LONG"]
    ov_short = breakdown_payload["overall"]["SHORT"]
    mae_all = breakdown_payload["mae_mfe_summary"]["all"]
    mae_win = breakdown_payload["mae_mfe_summary"]["winners"]
    mae_los = breakdown_payload["mae_mfe_summary"]["losers"]

    lines: list[str] = [
        "# TV28 (BTC-USDT / D1) — Diagnostic & Forensics Audit Summary",
        "",
        f"- **Run tham chiếu:** `V3PLUS43_ALLSIG_20261124T134936Z__PRUNE23_PLUS_TV20__BASE`",
        f"- **Chiến lược:** `TV28` (`L2_S1` SSL Hybrid QQE, `B0`, `V2_SL_TIGHTENING`: TP=3.0%, SL=1.2%, Warmup=120 D1 bars)",
        f"- **Cơ sở hạch toán:** `ALL_SIGNALS_PORTFOLIO` · `OWN_ENTRY_ONLY` (`pnl_own_entry`)",
        f"- **Khung 3Y:** `{core.START}` → `{core.END}` | **Forward:** `{fwd_meta['start_utc']}` → `{fwd_meta['end_utc']}`",
        f"- **Ngày xuất:** `{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`",
        "",
        "## 0. Đối soát Baseline (Reconciliation Check)",
        "",
        f"**Trạng thái tổng thể:** `{'PASS' if all_recon_pass else 'FAIL'}`",
        "",
        "| Chỉ số đối soát | Kỳ vọng (Baseline) | Tái lập thực tế | Chênh lệch | Kết quả |",
        "|---|---:|---:|---:|:---:|",
    ]
    for k, v in recon.items():
        lines.append(
            f"| `{k}` | {v['expected']:.8f} | {v['actual']:.8f} | {v['diff']:+.2e} | {'PASS' if v['pass'] else 'FAIL'} |"
        )
    lines += [
        "",
        f"> **Ghi chú kỹ thuật (Engine HEAD vs Manifest `4703ae2`):** {engine_refactor_info['note']}",
    ]

    lines += [
        "",
        "## 1. Test 1 — Trade-level Log, MAE/MFE & Forward (`tv28_trades_detail.csv`)",
        "",
        f"- **Số lệnh 3Y:** `{len(detail_3y)}` | **Số lệnh Forward (`{fwd_meta['start_utc'][:10]}` → `{fwd_meta['end_utc'][:10]}`):** `{len(detail_fwd)}` (signals={fwd_meta['tv28_signals']})",
        f"- **MAE/MFE trung bình (3Y):** MAE = `{mae_all['avg_mae_pct']:.3f}%` (`{mae_all['avg_mae_r']:.3f}R`), MFE = `{mae_all['avg_mfe_pct']:.3f}%` (`{mae_all['avg_mfe_r']:.3f}R`)",
        f"- **Lệnh thắng ({mae_win['count']}):** Avg MAE = `{mae_win['avg_mae_r']:.3f}R`, Avg MFE = `{mae_win['avg_mfe_r']:.3f}R`",
        f"- **Lệnh thua ({mae_los['count']}):** Avg MAE = `{mae_los['avg_mae_r']:.3f}R`, Avg MFE = `{mae_los['avg_mfe_r']:.3f}R` | Đạt MFE >= 1.0R rồi quay đầu thua: `{mae_los['losers_reached_1r_mfe_count']}/{mae_los['count']}` (MFE >= 1.5R: `{mae_los['losers_reached_1_5r_mfe_count']}`, MFE >= 2.0R: `{mae_los['losers_reached_2r_mfe_count']}`)",
        "",
        "## 2. Test 2 — Phân rã Long/Short, Split, Year & Exit Reason (`tv28_breakdown.json`)",
        "",
        "| Nhóm | Entries | Win Rate (%) | Net PnL ($) | Net PF | Avg Win ($) | Avg Loss ($) | Payoff Ratio |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def _fmt_row(label: str, s: dict[str, Any]) -> str:
        pr = f"{s['payoff_ratio']:.3f}" if s["payoff_ratio"] is not None else "N/A"
        return (
            f"| {label} | {s['entries']} | {s['win_rate_pct']:.2f}% | {s['net_pnl']:+.4f} | "
            f"{s['net_pf']:.3f} | {s['avg_win']:+.4f} | {s['avg_loss']:+.4f} | {pr} |"
        )

    lines.append(_fmt_row("**3Y ALL**", ov_all))
    lines.append(_fmt_row("3Y LONG", ov_long))
    lines.append(_fmt_row("3Y SHORT", ov_short))
    for sp in ("DEV", "VAL", "HOLDOUT"):
        for sd in ("ALL", "LONG", "SHORT"):
            lines.append(_fmt_row(f"{sp} · {sd}", breakdown_payload["by_split"][sp][sd]))
    for yr in ("Y1", "Y2", "Y3"):
        for sd in ("ALL", "LONG", "SHORT"):
            lines.append(_fmt_row(f"{yr} · {sd}", breakdown_payload["by_year"][yr][sd]))

    lines += [
        "",
        "### Phân bổ Exit Reason (3Y)",
        "",
        "| Exit Reason | Entries | Tỷ trọng (%) | Net PnL ($) | Avg PnL ($) | Avg Holding (D1 bars) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for rsn, st in by_exit_reason.items():
        lines.append(
            f"| `{rsn}` | {st['entries']} | {st['share_pct']:.2f}% | {st['net_pnl']:+.4f} | {st['avg_pnl']:+.4f} | {st['avg_holding_bars']:.2f} |"
        )

    lines += [
        "",
        "### Phân rã Regime (ADX14 & ATR% tại nến vào lệnh)",
        "",
        "| Regime Bucket | Entries | Win Rate (%) | Net PnL ($) | Net PF | LONG Net ($) | SHORT Net ($) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for grp_name in ("adx14", "atr_pct"):
        for lbl, blk in breakdown_payload["by_regime"][grp_name].items():
            a, lo, sh = blk["ALL"], blk["LONG"], blk["SHORT"]
            lines.append(
                f"| `{lbl}` | {a['entries']} | {a['win_rate_pct']:.2f}% | {a['net_pnl']:+.4f} | "
                f"{a['net_pf']:.3f} | {lo['net_pnl']:+.4f} ({lo['entries']}) | {sh['net_pnl']:+.4f} ({sh['entries']}) |"
            )

    lines += [
        "",
        "## 3. Test 3 — Cost Sensitivity riêng TV28 (`tv28_cost_sensitivity.json`)",
        "",
        f"- **Điểm hòa vốn slippage (exact bisection):** `{cost_payload['breakeven_slippage_bps']:.4f} bps` (Net PnL tại điểm này = `{cost_payload['breakeven_net_pnl_at_bps']:+.6f}`)",
        "",
        "| Profile | Slippage (bps/side) | Entries | Net PnL Own ($) | Net PF | Win Rate (%) | Closed MaxDD ($) | M2M MaxDD ($) | Fees ($) | Slippage ($) | Funding ($) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for prof in ("IDEAL", "BASE", "STRESS"):
        c = cost_profiles_out[prof]
        lines.append(
            f"| {prof} | {c['slippage_bps']:.1f} | {c['entries']} | {c['net_pnl']:+.4f} | {c['net_pf']:.3f} | "
            f"{c['win_rate_pct']:.2f}% | {c['max_dd_closed']:.4f} | {c['m2m_max_dd_dollars']:.4f} ({c['m2m_max_dd_pct']:.2f}%) | "
            f"{c['fees']:.4f} | {c['slippage']:.4f} | {c['funding']:.4f} |"
        )

    lines += [
        "",
        "## 4. Test 4 — Portfolio Ablation: Có TV28 vs Bỏ TV28 (`tv28_ablation.json`)",
        "",
        "| Window | Kịch bản | Entries | Net PnL VWAP ($) | Net PnL Own ($) | M2M MaxDD ($) | M2M MaxDD (%) | Closed MaxDD ($) | Profit Factor | Peak Concurrent Legs |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for wname in ("FULL_3Y", "DEV", "VAL", "HOLDOUT"):
        w = ablation_windows[wname]
        for sc_lbl, sc_key in (("FULL", "FULL_PRUNE23_PLUS_TV20"), ("LOO_NO_TV28", "LOO_NO_TV28")):
            x = w[sc_key]
            lines.append(
                f"| {wname} | {sc_lbl} | {x['entries']} | {x['net_pnl']:+.4f} | {x['net_pnl_own_entry']:+.4f} | {x['m2m_max_dd_dollars']:.4f} | "
                f"{x['m2m_max_dd_pct']:.2f}% | {x['closed_max_dd_dollars']:.4f} | {x['profit_factor']:.4f} | {x['peak_concurrent_legs']} |"
            )
        d = w["DELTA_WITH_TV28_MINUS_WITHOUT"]
        lines.append(
            f"| {wname} | **DELTA (Có - Bỏ)** | {d['entries']:+d} | {d['net_pnl']:+.4f} | {d['net_pnl_own_entry']:+.4f} | {d['m2m_max_dd_dollars']:+.4f} | "
            f"{d['m2m_max_dd_pct']:+.2f}% | {d['closed_max_dd_dollars']:+.4f} | {d['profit_factor']:+.4f} | {d['peak_concurrent_legs']:+d} |"
        )

    lines += [
        "",
        "## 5. Test 5 — Correlation & Overlap với TV27 (`tv28_vs_tv27_overlap.json`)",
        "",
        "### Tương quan Pearson chuỗi PnL (theo ngày/tuần đóng lệnh & theo ngày vào lệnh)",
        "",
        "| Cặp so sánh | Exit-Aligned Daily | Exit-Aligned Weekly | Entry-Aligned Daily | Entry-Aligned Weekly |",
        "|---|---:|---:|---:|---:|",
    ]
    for pair_key, pair_lbl in (
        ("TV28_vs_TV27", "TV28 vs TV27"),
        ("TV28_vs_PORTFOLIO_FULL", "TV28 vs FULL Portfolio"),
        ("TV28_vs_PORTFOLIO_EX_TV28", "TV28 vs Portfolio (ex-TV28)"),
    ):
        ed = correlations["exit_date_aligned"]["daily"][pair_key]
        ew = correlations["exit_date_aligned"]["weekly"][pair_key]
        nd = correlations["entry_date_aligned"]["daily"][pair_key]
        nw = correlations["entry_date_aligned"]["weekly"][pair_key]
        lines.append(
            f"| {pair_lbl} | {ed:+.4f} | {ew:+.4f} | {nd:+.4f} | {nw:+.4f} |"
        )

    lines += [
        "",
        "### Trùng thời gian giữ lệnh TV28 với TV27 (41 lệnh TV28 vs 51 lệnh TV27)",
        "",
        "| Phân nhóm trùng lệnh (Exclusive) | Số lệnh TV28 | Tỷ trọng (%) | Tổng Net PnL ($) | Win Rate (%) |",
        "|---|---:|---:|---:|---:|",
    ]
    for gkey, glbl in (
        ("no_overlap", "Không trùng TV27"),
        ("same_side_only", "Trùng cùng chiều (chỉ Same-side)"),
        ("opposite_side_only", "Trùng ngược chiều (chỉ Opposite-side)"),
        ("both_sides", "Trùng cả 2 chiều"),
    ):
        g = overlap_payload["tv28_overlap_groups_exclusive"][gkey]
        lines.append(f"| {glbl} | {g['count']} | {g['share_pct']:.2f}% | {g['net_pnl']:+.4f} | {g['win_rate_pct']:.2f}% |")

    ov_inc = overlap_payload["tv28_overlap_groups_inclusive"]
    lines += [
        "",
        f"- **Tổng hợp (Inclusive):** Có trùng với TV27 = `{ov_inc['any_overlap_with_tv27']['count']}/41` lệnh (`Net PnL = {ov_inc['any_overlap_with_tv27']['net_pnl']:+.4f}`); Trùng cùng chiều = `{ov_inc['overlaps_same_side_with_tv27']['count']}` (`Net PnL = {ov_inc['overlaps_same_side_with_tv27']['net_pnl']:+.4f}`); Trùng ngược chiều = `{ov_inc['overlaps_opposite_side_with_tv27']['count']}` (`Net PnL = {ov_inc['overlaps_opposite_side_with_tv27']['net_pnl']:+.4f}`).",
        "",
    ]

    (OUT_DIR / "TV28_FORENSICS_SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"DONE. Reports saved to {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
