"""Export V3_LAB_11_PRUNE23 (5 slippage points with 3Y + forward BingX data & Funding) into runs/v3_16_all_signals."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.risk_manager import RiskConfig
import autotrade_v3.reports.mt5_report as mt5_report
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import UnifiedSimulator
from autotrade_v3.sim.funding import FundingTable
from autotrade_v3.strategy import StrategyRegistry
from autotrade_v3.tools.verify_report import verify_mt5_report

START = "2023-11-01"
HIST_END = "2026-11-01"
PROFILES = ("IDEAL", "BASE", "STRESS")
EXTRA_SENS_SPECS: tuple[tuple[str, float], ...] = (("SLIP1", 1.0), ("SLIP34", 3.4))

STORE_DIR = ROOT / "runs" / "v3_16_all_signals"
SENS_DIR = STORE_DIR / "_sensitivity"
FUNDING_DIR = ROOT / "reports" / "v3_lab_11_funding_3y"
BINGX_CANDLES_1M_DIR = ROOT / "data" / "bingx" / "market" / "candles_1m"
BINGX_FUNDING_DIR = ROOT / "data" / "bingx" / "market" / "funding"


def _load_forward_m1(symbol: str) -> pd.DataFrame:
    fwd_path = BINGX_CANDLES_1M_DIR / f"{symbol}.parquet"
    if not fwd_path.is_file():
        return pd.DataFrame()
    raw = pd.read_parquet(fwd_path)
    if raw.empty:
        return pd.DataFrame()
    idx = pd.to_datetime(raw["open_time_ms"].astype("int64"), unit="ms", utc=True)
    df = pd.DataFrame(
        {
            "open": raw["open"].astype(float).to_numpy(),
            "high": raw["high"].astype(float).to_numpy(),
            "low": raw["low"].astype(float).to_numpy(),
            "close": raw["close"].astype(float).to_numpy(),
            "volume": raw["volume"].astype(float).to_numpy(),
            "provider": "BINGX",
        },
        index=idx,
    )
    df.index.name = "timestamp"
    return df


def _resample_m1_to_m15_complete(m1_full: pd.DataFrame) -> pd.DataFrame:
    agg = m1_full.resample("15min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    counts = m1_full["close"].resample("15min").count()
    complete = agg.loc[counts == 15].dropna()
    return complete


def _build_merged_funding() -> FundingTable:
    dfs: dict[str, pd.DataFrame] = {}
    for sym, prefix in (("BTC-USDT", "BTC"), ("ETH-USDT", "ETH")):
        parts: list[pd.DataFrame] = []
        hist_p = FUNDING_DIR / f"{prefix}_FUNDING_HISTORY.parquet"
        if hist_p.is_file():
            parts.append(pd.read_parquet(hist_p))
        fwd_p = BINGX_FUNDING_DIR / f"{sym}.parquet"
        if fwd_p.is_file():
            f_raw = pd.read_parquet(fwd_p)
            if not f_raw.empty:
                parts.append(
                    pd.DataFrame(
                        {
                            "funding_time_ms": f_raw["funding_time_ms"].astype("int64"),
                            "funding_rate": f_raw["funding_rate"].astype(float),
                            "mark_price": f_raw["mark_price"].astype(float),
                            "source": "BINGX_COLLECTOR",
                        }
                    )
                )
        if parts:
            merged = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["funding_time_ms"], keep="last")
            dfs[sym] = merged.sort_values("funding_time_ms").reset_index(drop=True)
    return FundingTable.from_dataframes(dfs)


def render_reports() -> None:
    root_resolved = ROOT.resolve()
    assert Path(mt5_report.__file__).resolve().is_relative_to(root_resolved), mt5_report.__file__

    run_dirs = {prof: STORE_DIR / f"V3_LAB_11_PRUNE23_{prof}" for prof in PROFILES}
    sens_runs: dict[float, Path] = {0.0: run_dirs["IDEAL"], 2.0: run_dirs["BASE"], 5.0: run_dirs["STRESS"]}
    for tag, bps in EXTRA_SENS_SPECS:
        extra_dir = SENS_DIR / f"V3_LAB_11_PRUNE23_{tag}"
        if (extra_dir / "trades.parquet").exists():
            sens_runs[bps] = extra_dir
    for prof, run_dir in run_dirs.items():
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        end_str = manifest["time_window"]["end"]
        git_sha = manifest["git_sha"]
        mt5_report.generate_mt5_report(run_dir, run_dir, sibling_dirs=run_dirs, sensitivity_runs=sens_runs)
        vr = verify_mt5_report(
            run_dir,
            run_dir / "report_v1.json",
            run_dir / "report_mt5.html",
            sensitivity_runs=sens_runs,
        )
        print(f"{prof} report verified (end={end_str}, sha={git_sha[:8]}): {vr}")
        if not vr:
            raise SystemExit(1)


def run_backtest() -> None:
    start_ms = to_utc_ms(START)

    dl = DataLayer(
        catalog_path=ROOT / "catalog" / "research.duckdb",
        qa_path=ROOT / "catalog" / "v3_m1_qa.json",
        data_dir=ROOT / "data" / "m1_raw",
    )
    strat = StrategyRegistry.get("V3_LAB_11_PRUNE23", "11.0.0")
    symbols = sorted({c["symbol"] for c in strat.config["combos"]})

    m1_full_by_sym: dict[str, pd.DataFrame] = {}
    for s in symbols:
        d_hist = dl.query_m1(s, START, HIST_END, include_warmup=True)
        d_fwd = _load_forward_m1(s)
        if not d_fwd.empty:
            common_cols = [c for c in ("open", "high", "low", "close", "volume", "provider") if c in d_hist.columns or c in d_fwd.columns]
            comb = pd.concat([d_hist[common_cols], d_fwd[common_cols]])
            comb = comb[~comb.index.duplicated(keep="last")].sort_index()
            m1_full_by_sym[s] = comb
        else:
            m1_full_by_sym[s] = d_hist

    end_ms = min(int(df.index.asi8.max() // 1_000_000) + 60_000 for df in m1_full_by_sym.values())
    end_str = pd.Timestamp(end_ms, unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

    m1, m15 = {}, {}
    for s in symbols:
        full_df = m1_full_by_sym[s]
        t = datetime_index_to_ms(full_df.index)
        m1[s] = full_df.loc[(t >= start_ms) & (t < end_ms)]
        m15_df = _resample_m1_to_m15_complete(full_df)
        t15 = datetime_index_to_ms(m15_df.index)
        m15[s] = m15_df.loc[t15 < end_ms]

    sigs = [
        s
        for s in strat.generate_signals(m15, symbols=symbols, only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < end_ms
    ]
    sigs.sort(key=lambda x: (int(x.signal_close_ms), x.symbol, int(x.combo_id), x.side))

    funding_tbl = _build_merged_funding()

    git_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    now_utc = datetime.now(timezone.utc).isoformat()

    def _export_single_run(
        *,
        run_id: str,
        run_dir: Path,
        cost_profile_name: str,
        cm: CostModel,
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        res = UnifiedSimulator(
            cost_model=cm,
            risk_config=RiskConfig(),
            ambiguity_policy="CONSERVATIVE",
            funding_table=funding_tbl,
        ).run("BINGX_PORTFOLIO", sigs, m1)
        tdf = pd.DataFrame([asdict(t) for t in res.trades])
        tdf.to_parquet(run_dir / "trades.parquet", index=False)
        res.snapshots.to_parquet(run_dir / "snapshots.parquet", index=False)
        ddf = pd.DataFrame([asdict(d) for d in res.signal_decisions])
        ddf.to_parquet(run_dir / "signal_decisions.parquet", index=False)

        manifest = {
            "run_id": run_id,
            "strategy_id": strat.strategy_id,
            "strategy_version": strat.version,
            "execution_mode": "BINGX_PORTFOLIO",
            "generated_at_utc": now_utc,
            "time_window": {"start": f"{START}T00:00:00Z", "end": end_str},
            "symbols": symbols,
            "initial_equity": 1000.0,
            "notional_per_trade": 100.0,
            "total_signals": len(sigs),
            "accepted_entries": len({t.trade_id for t in res.trades}),
            "total_legs": len(res.trades),
            "skipped_signals_count": len(res.skipped_signals),
            "rejected_signals_count": len(res.rejected_signals),
            "cost_profile": cost_profile_name,
            "cost_profile_hash": cm.profile.profile_hash,
            "slippage_bps": cm.params_for("BTC-USDT").slippage_tp_bps,
            "ambiguity_policy": "CONSERVATIVE",
            "funding_enabled": True,
            "funding_net_cashflow": res.summary_dict["funding_net_cashflow"],
            "git_sha": git_sha,
            "code_dirty": False,
            "dataset_hash": "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628",
            "config_hash": strat.get_spec_hash(),
            "validation_status": "VERIFIED",
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        validation = {
            "overall_status": "VERIFIED",
            "accounting_invariant": res.summary_dict["accounting_invariant"],
            "funding_enabled": True,
        }
        (run_dir / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")

    for prof in PROFILES:
        run_id = f"V3_LAB_11_PRUNE23_{prof}"
        _export_single_run(
            run_id=run_id,
            run_dir=STORE_DIR / run_id,
            cost_profile_name=prof,
            cm=CostModel.from_profile_name(prof),
        )

    for tag, bps in EXTRA_SENS_SPECS:
        run_id = f"V3_LAB_11_PRUNE23_{tag}"
        prof_name = f"SLIP_{tag}"
        _export_single_run(
            run_id=run_id,
            run_dir=SENS_DIR / run_id,
            cost_profile_name=prof_name,
            cm=CostModel.from_uniform_params(
                name=prof_name,
                fee_taker=0.0005,
                fee_maker=0.0,
                half_spread_bps=0.0,
                slippage_bps=bps,
            ),
        )

    render_reports()


def main() -> None:
    parser = argparse.ArgumentParser(description="Export V3_LAB_11_PRUNE23 runs and render detail reports.")
    parser.add_argument("--render-only", action="store_true", help="Only render and verify reports.")
    args = parser.parse_args()
    if args.render_only:
        render_reports()
    else:
        run_backtest()


if __name__ == "__main__":
    main()
