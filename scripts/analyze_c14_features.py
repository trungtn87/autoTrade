"""Nhiệm vụ B: Feature diagnostics & M1 MAE/MFE analysis for BTC-USDT/C14 baseline trades."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from scripts.c14_filters import build_c14_1h_feature_table

BASELINE_RUNS_ROOT = Path("d:/AutoTrade/runs/fixed_tpsl_v1")
RUN_OTC_BASE = "RUN_20261115_042341_20FINAL_FIXED_V1_ONE_TRADE_PER_COMBO_20231101_20261101"
RUN_ALL_BASE = "RUN_20261115_041845_20FINAL_FIXED_V1_ALL_SIGNALS_20231101_20261101"
RUN_BP_BASE = "RUN_20261115_043025_20FINAL_FIXED_V1_BINGX_PORTFOLIO_20231101_20261101"


def _compute_mae_mfe(trades: pd.DataFrame, m1: pd.DataFrame) -> pd.DataFrame:
    idx_ns = m1.index.tz_convert("UTC").asi8 if m1.index.tz is not None else m1.index.tz_localize("UTC").asi8
    m1_ms = idx_ns // 1_000_000
    highs = m1["high"].to_numpy(float)
    lows = m1["low"].to_numpy(float)

    mfe_list: list[float] = []
    mae_list: list[float] = []
    mfe_bar_offset_list: list[int] = []

    for _, row in trades.iterrows():
        t0 = int(row["entry_time_ms"])
        t1 = int(row["exit_time_ms"])
        px0 = float(row["entry_px_fill"])
        side = str(row["side"])

        i0 = int(np.searchsorted(m1_ms, t0, side="left"))
        i1 = int(np.searchsorted(m1_ms, t1, side="right"))
        if i1 <= i0:
            mfe_list.append(0.0)
            mae_list.append(0.0)
            mfe_bar_offset_list.append(0)
            continue

        h_win = highs[i0:i1]
        l_win = lows[i0:i1]
        if side == "BUY":
            max_h = float(np.max(h_win))
            min_l = float(np.min(l_win))
            mfe = max(0.0, (max_h - px0) / px0)
            mae = max(0.0, (px0 - min_l) / px0)
            mfe_idx = int(np.argmax(h_win))
        else:
            max_h = float(np.max(h_win))
            min_l = float(np.min(l_win))
            mfe = max(0.0, (px0 - min_l) / px0)
            mae = max(0.0, (max_h - px0) / px0)
            mfe_idx = int(np.argmin(l_win))

        mfe_list.append(mfe)
        mae_list.append(mae)
        mfe_bar_offset_list.append(mfe_idx)

    out = trades.copy()
    out["mfe_pct"] = mfe_list
    out["mae_pct"] = mae_list
    out["mfe_bar_offset_min"] = mfe_bar_offset_list
    return out


def _group_summary(df: pd.DataFrame, col: str) -> list[dict[str, Any]]:
    rows = []
    for k, g in df.groupby(col, sort=True, observed=False):
        wins = int((g["exit_reason"] == "TP").sum())
        n = int(len(g))
        rows.append(
            {
                col: str(k),
                "trades": n,
                "tp_count": wins,
                "sl_count": n - wins,
                "win_rate_pct": round((wins / n) * 100.0, 2) if n else 0.0,
                "net_pnl": round(float(g["net_pnl"].sum()), 4),
                "avg_pnl": round(float(g["net_pnl"].mean()), 4) if n else 0.0,
                "avg_overext_atr": round(float(g["dist_close_kijun_atr"].mean()), 4) if n else 0.0,
                "avg_kumo_pct": round(float(g["kumo_thickness_pct"].mean()) * 100.0, 4) if n else 0.0,
                "avg_atr_pct": round(float(g["atr14_pct"].mean()) * 100.0, 4) if n else 0.0,
            }
        )
    return rows


def main() -> None:
    out_dir = ROOT / "reports" / "v3-c14-loo-h2"
    out_dir.mkdir(parents=True, exist_ok=True)

    dl = DataLayer()
    df_1h = dl.query_timeframe("BTC-USDT", "1h", "2023-11-01", "2026-11-01", include_warmup=True)
    feat_1h = build_c14_1h_feature_table(df_1h)
    df_m1 = dl.query_m1("BTC-USDT", "2023-11-01", "2026-11-01", include_warmup=False)

    # Load OTC BASE trades (Own Entry PnL) and ALL_SIGNALS BASE
    otc_trades = pd.read_parquet(BASELINE_RUNS_ROOT / RUN_OTC_BASE / "trades.parquet")
    c14_otc = otc_trades[(otc_trades["symbol"] == "BTC-USDT") & (otc_trades["combo_id"] == 104)].copy()

    bp_trades = pd.read_parquet(BASELINE_RUNS_ROOT / RUN_BP_BASE / "trades.parquet")
    c14_bp = bp_trades[(bp_trades["symbol"] == "BTC-USDT") & (bp_trades["combo_id"] == 104)][
        ["signal_id", "pnl_vwap"]
    ].rename(columns={"pnl_vwap": "bp_pnl_vwap"})
    c14_otc = c14_otc.merge(c14_bp, on="signal_id", how="left")

    # Signal close ms is embedded at the end of signal_id
    c14_otc["signal_close_ms"] = c14_otc["signal_id"].str.rsplit("_", n=1).str[-1].astype("int64")

    feat_cols = [
        "close",
        "tenkan",
        "kijun",
        "span_a",
        "span_b",
        "kumo_top",
        "kumo_bottom",
        "kumo_thickness",
        "kumo_thickness_pct",
        "atr14",
        "atr14_pct",
        "dist_close_kijun",
        "abs_dist_close_kijun",
        "dist_close_kijun_atr",
        "dist_close_kumo",
        "abs_dist_close_kumo_pct",
    ]
    df = c14_otc.merge(
        feat_1h[feat_cols],
        left_on="signal_close_ms",
        right_index=True,
        how="left",
    )
    assert df["tenkan"].notna().all(), "Missing 1H feature alignment for some C14 trades!"

    df = _compute_mae_mfe(df, df_m1)

    dt_entry = pd.to_datetime(df["entry_time_ms"], unit="ms", utc=True)
    df["year"] = dt_entry.dt.year
    df["quarter"] = dt_entry.dt.to_period("Q").astype(str)
    df["hold_minutes"] = df["bars_held"].astype(int)
    df["is_win"] = df["exit_reason"] == "TP"
    df["tk_spread_atr"] = (df["tenkan"] - df["kijun"]).abs() / df["atr14"]

    # Volatility regime (terciles of atr14_pct across C14 trades)
    df["vol_regime"] = pd.qcut(df["atr14_pct"], q=3, labels=["LOW_VOL", "MID_VOL", "HIGH_VOL"])
    df["overext_bucket"] = pd.cut(
        df["dist_close_kijun_atr"],
        bins=[0.0, 0.75, 1.5, 2.5, 100.0],
        labels=["<=0.75", "0.75-1.5", "1.5-2.5", ">2.5"],
    )
    df["kumo_bucket"] = pd.cut(
        df["kumo_thickness_pct"] * 100.0,
        bins=[0.0, 0.5, 1.0, 2.0, 100.0],
        labels=["<0.5%", "0.5-1.0%", "1.0-2.0%", ">2.0%"],
    )

    df.to_csv(out_dir / "c14_feature_diagnostics_trades.csv", index=False)

    wins_df = df[df["is_win"]]
    loss_df = df[~df["is_win"]]

    summary: dict[str, Any] = {
        "total_c14_otc_trades": int(len(df)),
        "tp_count": int(len(wins_df)),
        "sl_count": int(len(loss_df)),
        "win_rate_pct": round(float(df["is_win"].mean()) * 100.0, 2),
        "own_net_pnl_base": round(float(df["net_pnl"].sum()), 4),
        "vwap_net_pnl_base": round(float(df["bp_pnl_vwap"].sum()), 4),
        "winners_vs_losers": {
            "feature": [
                "dist_close_kijun_atr",
                "abs_dist_close_kumo_pct",
                "kumo_thickness_pct",
                "atr14_pct",
                "tk_spread_atr",
                "hold_minutes",
                "mfe_pct",
                "mae_pct",
            ],
            "winners_mean": {
                col: round(float(wins_df[col].mean()), 6)
                for col in [
                    "dist_close_kijun_atr",
                    "abs_dist_close_kumo_pct",
                    "kumo_thickness_pct",
                    "atr14_pct",
                    "tk_spread_atr",
                    "hold_minutes",
                    "mfe_pct",
                    "mae_pct",
                ]
            },
            "winners_median": {
                col: round(float(wins_df[col].median()), 6)
                for col in [
                    "dist_close_kijun_atr",
                    "abs_dist_close_kumo_pct",
                    "kumo_thickness_pct",
                    "atr14_pct",
                    "tk_spread_atr",
                    "hold_minutes",
                    "mfe_pct",
                    "mae_pct",
                ]
            },
            "losers_mean": {
                col: round(float(loss_df[col].mean()), 6)
                for col in [
                    "dist_close_kijun_atr",
                    "abs_dist_close_kumo_pct",
                    "kumo_thickness_pct",
                    "atr14_pct",
                    "tk_spread_atr",
                    "hold_minutes",
                    "mfe_pct",
                    "mae_pct",
                ]
            },
            "losers_median": {
                col: round(float(loss_df[col].median()), 6)
                for col in [
                    "dist_close_kijun_atr",
                    "abs_dist_close_kumo_pct",
                    "kumo_thickness_pct",
                    "atr14_pct",
                    "tk_spread_atr",
                    "hold_minutes",
                    "mfe_pct",
                    "mae_pct",
                ]
            },
        },
        "sl_favorable_excursion": {
            "total_sl": int(len(loss_df)),
            "sl_mfe_ge_0_3pct": int((loss_df["mfe_pct"] >= 0.003).sum()),
            "sl_mfe_ge_0_5pct": int((loss_df["mfe_pct"] >= 0.005).sum()),
            "sl_mfe_ge_1_0pct": int((loss_df["mfe_pct"] >= 0.010).sum()),
            "sl_mfe_ge_1_5pct": int((loss_df["mfe_pct"] >= 0.015).sum()),
            "sl_mfe_mean_pct": round(float(loss_df["mfe_pct"].mean()) * 100.0, 4),
            "sl_mfe_median_pct": round(float(loss_df["mfe_pct"].median()) * 100.0, 4),
        },
        "by_side": _group_summary(df, "side"),
        "by_year": _group_summary(df, "year"),
        "by_vol_regime": _group_summary(df, "vol_regime"),
        "by_overext_bucket": _group_summary(df, "overext_bucket"),
        "by_kumo_bucket": _group_summary(df, "kumo_bucket"),
        "h2_filter_coverage_on_c14_otc": {
            "h2a_pass_count": int((df["dist_close_kijun_atr"] <= 1.5).sum()),
            "h2a_drop_count": int((df["dist_close_kijun_atr"] > 1.5).sum()),
            "h2b_pass_count": int((df["kumo_thickness_pct"] >= 0.005).sum()),
            "h2b_drop_count": int((df["kumo_thickness_pct"] < 0.005).sum()),
            "h2ab_pass_count": int(((df["dist_close_kijun_atr"] <= 1.5) & (df["kumo_thickness_pct"] >= 0.005)).sum()),
        },
    }

    (out_dir / "c14_feature_diagnostics_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
