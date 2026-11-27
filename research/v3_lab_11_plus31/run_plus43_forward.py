"""Forward post-2026-10-31 ALL_SIGNALS_PORTFOLIO simulation for PLUS43 (PRUNE23 + TV20)."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_backtest as core
import run_plus44 as plus44
import run_plus44_all_signals as p44_all
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.sim.engine import UnifiedSimulator

FORWARD_START_ISO = "2026-11-01T00:00:00Z"
FORWARD_START_MS = int(pd.Timestamp(FORWARD_START_ISO).value // 10**6)
WARMUP_START_ISO = "2026-01-01"
SIM_M1_START_MS = int(pd.Timestamp("2026-10-31T00:00:00Z").value // 10**6)
PROFILES: tuple[str, ...] = ("IDEAL", "BASE", "STRESS")
TV20_KEPT: set[str] = set(plus44.KEPT) - {"TV13"}
OUT_JSON = LAB / "outputs" / "plus43_all_signals" / "FORWARD_POST_3Y.json"


def _load_combined_m1(data_root: Path) -> tuple[dict[str, pd.DataFrame], int]:
    """Load 2026-01-01..2026-11-01 warmup M1 + BingX forward M1 from data_root."""
    layer = core.DataLayer()
    combined: dict[str, pd.DataFrame] = {}
    max_ms_list: list[int] = []
    for sym in ("BTC-USDT", "ETH-USDT"):
        warm = layer.query_m1(sym, WARMUP_START_ISO, "2026-11-01")
        fwd_path = data_root / "market" / "candles_1m" / f"{sym}.parquet"
        if not fwd_path.is_file():
            raise FileNotFoundError(f"Thiếu dữ liệu M1 forward: {fwd_path}")
        raw = pq.read_table(fwd_path).to_pandas()
        raw = raw.sort_values("open_time_ms").drop_duplicates("open_time_ms", keep="last")
        fwd = pd.DataFrame(
            {
                "open": raw["open"].astype(float).to_numpy(),
                "high": raw["high"].astype(float).to_numpy(),
                "low": raw["low"].astype(float).to_numpy(),
                "close": raw["close"].astype(float).to_numpy(),
                "volume": raw["volume"].astype(float).to_numpy(),
                "provider": raw.get("provenance", pd.Series(["bingx"] * len(raw))).astype(str).to_numpy(),
            },
            index=pd.to_datetime(raw["open_time_ms"].astype(np.int64), unit="ms", utc=True),
        )
        merged = pd.concat([warm, fwd])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        combined[sym] = merged
        max_ms_list.append(int(merged.index[-1].value // 10**6))
    return combined, min(max_ms_list)


def _load_forward_funding(data_root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for sym in ("BTC-USDT", "ETH-USDT"):
        fpath = data_root / "market" / "funding" / f"{sym}.parquet"
        if not fpath.is_file():
            continue
        df = pq.read_table(fpath).to_pandas()
        if df.empty:
            continue
        frames.append(
            pd.DataFrame({
                "symbol": sym,
                "funding_time_ms": df["funding_time_ms"].astype(np.int64),
                "funding_rate": df["funding_rate"].astype(float),
            })
        )
    if not frames:
        return pd.DataFrame(columns=["symbol", "funding_time_ms", "funding_rate"])
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["symbol", "funding_time_ms"], keep="last"
    )


def _resample_closed_safe(m1_df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    core.configure_tv_lab()
    bucket_ms = minutes * 60_000
    t_ms = m1_df.index.asi8 // 1_000_000
    last_complete_end_ms = ((int(t_ms[-1]) + 60_000) // bucket_ms) * bucket_ms
    trimmed = m1_df.iloc[t_ms < last_complete_end_ms]
    return core.tvlab.resample_m1_closed(trimmed, minutes)


def run_forward(
    data_root: Path,
    out_json: Path = OUT_JSON,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    core.configure_tv_lab()
    m1_full, max_m1_ms = _load_combined_m1(data_root)
    fwd_funding_tbl = _load_forward_funding(data_root)
    if registry_path is not None:
        funding_tbl = pd.concat([core.tvlab.funding_table(), fwd_funding_tbl], ignore_index=True).drop_duplicates(
            subset=["symbol", "funding_time_ms"], keep="last"
        )
        registry = pd.read_csv(registry_path).sort_values("display_id").reset_index(drop=True)
    else:
        funding_tbl = fwd_funding_tbl
        full_registry = core.load_registry()
        registry = (
            full_registry.loc[full_registry.display_id.isin(TV20_KEPT)]
            .sort_values("display_id")
            .reset_index(drop=True)
        )

    m15 = {sym: _resample_closed_safe(df, 15) for sym, df in m1_full.items()}
    core.ensure_fixed_v1_registered()
    prune = core.V3Lab11Prune23Strategy()
    prune_signals = [
        s
        for s in prune.generate_signals(m15, symbols=list(m1_full), only_approved=True, only_warmup_ready=True)
        if int(s.signal_close_ms) + 1 >= FORWARD_START_MS
    ]

    tv_signals: list[Any] = []
    for row in registry.to_dict("records"):
        sym = str(row["symbol"])
        bars = _resample_closed_safe(m1_full[sym], int(row["timeframe_minutes"]))
        evaluated = core.tvlab.PORT_CLASSES[str(row["strategy_id"])]().evaluate(bars, str(row["variant"]))
        warmup = int(row["warmup_bars"])
        if warmup and len(evaluated) > warmup:
            evaluated.loc[evaluated.index[:warmup], "signal"] = 0
        evaluated = evaluated.join(bars[["open", "high", "low", "close", "volume", "provider"]])
        transformed = core.tvlab.apply_variant(evaluated, str(row["variant_id"]))
        sigs = core.tvlab.make_signals(
            transformed, row, float(row["tp_pct"]), float(row["sl_pct"]), str(row["variant_id"])
        )
        numeric_id = 31000 + int(str(row["display_id"])[2:])
        for s in sigs:
            if int(s.signal_close_ms) + 1 < FORWARD_START_MS:
                continue
            tv_signals.append(
                replace(
                    s,
                    combo_id=numeric_id,
                    combo_name=str(row["display_id"]),
                    signal_id=(
                        f"{row['display_id']}|{row['run_id']}|{row['strategy_source_sha256']}|"
                        f"{s.signal_close_ms}|{s.side}"
                    ),
                )
            )

    all_signals = prune_signals + tv_signals
    sim_start_ts = pd.to_datetime(SIM_M1_START_MS, unit="ms", utc=True)
    m1_sim = {sym: df.loc[df.index >= sim_start_ts] for sym, df in m1_full.items()}

    profiles_out: dict[str, Any] = {}
    for prof in PROFILES:
        model = core.tvlab.SignedFundingCostModel.from_profile_name(
            prof, is_real_data=True, funding_table=funding_tbl
        )
        risk = RiskConfig(
            initial_equity=core.INITIAL_EQUITY,
            notional_per_trade=core.NOTIONAL,
            leverage_btc=100,
            leverage_eth=50,
            cost_profile=prof,
            cost_model=model,
        )
        res = UnifiedSimulator(
            risk_config=risk,
            final14_exit_mode="100_HARD",
            cost_model=model,
            ambiguity_policy="CONSERVATIVE",
        ).run("ALL_SIGNALS_PORTFOLIO", all_signals, m1_sim, sample_snapshot_interval_m1=15)

        _, raw_frame = core.result_payload(res)
        frame = core.normalized_trades(raw_frame, "PRUNE23_PLUS_TV20_FORWARD", prof)
        snaps = res.snapshots.copy()
        if registry_path is not None and not frame.empty and "pnl_own_entry" in frame.columns:
            frame_own = frame.copy()
            frame_own["net_pnl"] = frame_own["pnl_own_entry"].astype(float)
            stats = p44_all._summarize_window(frame_own, snaps, use_global_m2m=False)
            stats["net_pnl_vwap"] = float(frame["net_pnl"].astype(float).sum())
        else:
            stats = p44_all._summarize_window(frame, snaps, use_global_m2m=False)
        eq_df = core.daily_equity(snaps, "PRUNE23_PLUS_TV20_FORWARD", prof)
        daily_fwd: list[dict[str, Any]] = []
        if not eq_df.empty:
            eq_fwd = eq_df.loc[eq_df["timestamp_ms"].astype(np.int64) >= FORWARD_START_MS]
            daily_fwd = [
                {
                    "timestamp_ms": int(r["timestamp_ms"]),
                    "net_pnl": float(r["equity"]) - float(core.INITIAL_EQUITY),
                }
                for _, r in eq_fwd.iterrows()
            ]
        per_combo = (
            frame.groupby("display_id")["pnl_own_entry"].sum().to_dict()
            if not frame.empty
            else {}
        )
        profiles_out[prof] = {
            **stats,
            "daily_equity": daily_fwd,
            "per_combo_own_entry": {str(k): float(v) for k, v in per_combo.items()},
        }

    end_utc = pd.to_datetime(max_m1_ms, unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "study_group": "PRUNE23_PLUS_TV20",
        "version_tag": "PLUS43_V2_1_TP_ONLY" if registry_path is not None else "PLUS43_BASELINE",
        "mode": "ALL_SIGNALS_PORTFOLIO",
        "start_utc": FORWARD_START_ISO,
        "end_utc": end_utc,
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signals_total": len(all_signals),
        "signals_prune23": len(prune_signals),
        "signals_tv20": len(tv_signals),
        "profiles": profiles_out,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chạy forward PLUS43 sau 2026-10-31 từ data/bingx/market.")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data" / "bingx")
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--registry", type=Path, default=None)
    args = parser.parse_args(argv)
    payload = run_forward(args.data_root, args.out_json, registry_path=args.registry)
    b = payload["profiles"]["BASE"]
    print(
        f"FORWARD PLUS43 ({payload['start_utc'][:10]} -> {payload['end_utc'][:10]}) | "
        f"BASE trades={b['executed_trades']} net={b['net_pnl']:+.2f} m2m_dd={b['m2m_dd']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
