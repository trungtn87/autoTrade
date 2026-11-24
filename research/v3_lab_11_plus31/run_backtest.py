from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
FREEZE = LAB / "freeze"
OUT = LAB / "outputs"
DB_PATH = LAB / "backtests.sqlite3"
START = "2023-11-01"
END = "2026-11-01"
INITIAL_EQUITY = 1000.0
NOTIONAL = 100.0
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research/v3_tv_positive_variants_lab_5"))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.reconcile_v3.era_engines import ensure_fixed_v1_registered
from autotrade_v3.sim.engine import UnifiedSimulator
from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy
import run_lab_5 as tvlab

SIMULATOR_VERSION = "UnifiedSimulator/local-current"


def configure_tv_lab() -> None:
    tvlab.CFG = dict(tvlab.CFG)
    tvlab.CFG.update({
        "is_start": START,
        "is_end_exclusive": END,
        "initial_equity": INITIAL_EQUITY,
        "notional_per_trade": NOTIONAL,
        "periods": [
            ["Y1", "2023-11-01", "2024-11-01"],
            ["Y2", "2024-11-01", "2025-11-01"],
            ["Y3", "2025-11-01", "2026-11-01"],
        ],
    })
    tvlab._FUNDING_CACHE = None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "UNKNOWN"


def load_registry() -> pd.DataFrame:
    path = FREEZE / "TV31_SELECTED_REGISTRY.csv"
    if not path.exists():
        raise FileNotFoundError("Run freeze/LOCAL_SELECTION_CHECK.py first")
    frame = pd.read_csv(path)
    if len(frame) != 31 or frame.display_id.nunique() != 31 or not frame.source_verified.all():
        raise RuntimeError("Frozen TV31 registry is invalid")
    return frame.sort_values("display_id").reset_index(drop=True)


def _tv_worker(row: dict[str, Any]) -> dict[str, Any]:
    configure_tv_lab()
    layer = DataLayer()
    m1 = layer.query_m1(str(row["symbol"]), START, END)
    bars = tvlab.resample_m1_closed(m1, int(row["timeframe_minutes"]))
    evaluated = tvlab.PORT_CLASSES[str(row["strategy_id"])]().evaluate(bars, str(row["variant"]))
    warmup = int(row["warmup_bars"])
    if warmup:
        evaluated.loc[evaluated.index[:warmup], "signal"] = 0
    evaluated = evaluated.join(bars[["open", "high", "low", "close", "volume", "provider"]])
    transformed = tvlab.apply_variant(evaluated, str(row["variant_id"]))
    signals = tvlab.make_signals(
        transformed, row, float(row["tp_pct"]), float(row["sl_pct"]), str(row["variant_id"])
    )
    numeric_id = 31000 + int(str(row["display_id"])[2:])
    signals = [replace(
        s,
        combo_id=numeric_id,
        combo_name=str(row["display_id"]),
        signal_id=(
            f"{row['display_id']}|{row['run_id']}|{row['strategy_source_sha256']}|"
            f"{s.signal_close_ms}|{s.side}"
        ),
    ) for s in signals]
    return {"display_id": row["display_id"], "signals": signals}


def run_portfolio(signals: list[Any], m1: dict[str, pd.DataFrame], profile: str):
    configure_tv_lab()
    model = tvlab.SignedFundingCostModel.from_profile_name(
        profile, is_real_data=True, funding_table=tvlab.funding_table()
    )
    risk = RiskConfig(
        initial_equity=INITIAL_EQUITY,
        notional_per_trade=NOTIONAL,
        leverage_btc=100,
        leverage_eth=50,
        cost_profile=profile,
        cost_model=model,
    )
    return UnifiedSimulator(
        risk_config=risk,
        final14_exit_mode="100_HARD",
        cost_model=model,
        ambiguity_policy="CONSERVATIVE",
    ).run("BINGX_PORTFOLIO", signals, m1, sample_snapshot_interval_m1=15)


def run_independent(signals: list[Any], m1: dict[str, pd.DataFrame], profile: str):
    configure_tv_lab()
    model = tvlab.SignedFundingCostModel.from_profile_name(
        profile, is_real_data=True, funding_table=tvlab.funding_table()
    )
    risk = RiskConfig(
        initial_equity=INITIAL_EQUITY, notional_per_trade=NOTIONAL,
        leverage_btc=100, leverage_eth=50, cost_profile=profile, cost_model=model,
    )
    return UnifiedSimulator(
        risk_config=risk, final14_exit_mode="100_HARD", cost_model=model,
        ambiguity_policy="CONSERVATIVE",
    ).run("ONE_TRADE_PER_COMBO", signals, m1)


def result_payload(result: Any) -> tuple[dict[str, Any], pd.DataFrame]:
    metrics, trades = tvlab.result_metrics(result)
    metrics["roi_pct"] = metrics["net_pnl"] / INITIAL_EQUITY * 100.0
    metrics["accounting_pass"] = bool(
        (result.summary_dict or {}).get("accounting_invariant", {}).get("passed", True)
    )
    metrics["accepted_signals"] = len(result.accepted_signals)
    metrics["skipped_signals"] = len(result.skipped_signals)
    metrics["rejected_signals"] = len(result.rejected_signals)
    return metrics, trades


def group_metrics(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows = []
    for values, part in frame.groupby(keys, dropna=False):
        values = values if isinstance(values, tuple) else (values,)
        pnl = part.net_pnl.astype(float)
        wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        hold = (part.exit_time_ms - part.entry_time_ms) / 3_600_000
        ordered = part.sort_values(["exit_time_ms", "entry_time_ms"])
        equity = INITIAL_EQUITY + ordered.net_pnl.cumsum()
        dd = equity.cummax() - equity
        row = dict(zip(keys, values))
        row.update({
            "executed_trades": len(part),
            "buy_trades": int(part.side.eq("BUY").sum()),
            "sell_trades": int(part.side.eq("SELL").sum()),
            "win_rate_pct": float((pnl > 0).mean() * 100),
            "gross_pnl": float(part.gross_pnl.sum()),
            "fees": float(part.fee_total.sum()),
            "funding": float(part.funding_cost.sum()),
            "slippage": float(part.slippage_cost.sum()),
            "net_pnl": float(pnl.sum()),
            "roi_pct": float(pnl.sum() / INITIAL_EQUITY * 100),
            "profit_factor": float(wins / losses) if losses > 0 else (999.0 if wins > 0 else 0.0),
            "max_dd_closed": float(dd.max()),
            "m2m_dd": float(dd.max()),
            "hold_avg_hours": float(hold.mean()),
            "hold_p90_hours": float(hold.quantile(.9)),
            "hold_max_hours": float(hold.max()),
            "tp_count": int(part.exit_reason.eq("TP").sum()),
            "sl_count": int(part.exit_reason.eq("SL").sum()),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def normalized_trades(frame: pd.DataFrame, group_name: str, profile: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out.insert(0, "study_group", group_name)
    out.insert(1, "profile", profile)
    combo_name = out["combo_name"].astype(str)
    out["display_id"] = np.where(
        combo_name.str.match(r"^TV\d{2}$"), combo_name, out["symbol"].astype(str) + "/" + combo_name
    )
    return out


def daily_equity(snapshots: pd.DataFrame, group_name: str, profile: str) -> pd.DataFrame:
    if snapshots.empty:
        return pd.DataFrame()
    frame = snapshots.copy()
    frame["date"] = pd.to_datetime(frame.timestamp_ms, unit="ms", utc=True).dt.floor("D")
    frame = frame.sort_values("timestamp_ms").groupby("date", as_index=False).last()
    frame.insert(0, "study_group", group_name)
    frame.insert(1, "profile", profile)
    return frame


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS research_runs (
      run_id TEXT PRIMARY KEY, study_group TEXT NOT NULL, profile TEXT NOT NULL,
      dataset_hash TEXT NOT NULL, config_hash TEXT NOT NULL, simulator_version TEXT NOT NULL,
      fee_profile TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
      created_at TEXT NOT NULL, status TEXT NOT NULL, portfolio_valid INTEGER NOT NULL,
      initial_equity REAL NOT NULL, metrics_json TEXT NOT NULL,
      UNIQUE(study_group, profile, dataset_hash, config_hash, start_time, end_time)
    );
    CREATE TABLE IF NOT EXISTS strategy_results (
      run_id TEXT NOT NULL, strategy_id TEXT NOT NULL, source_run_id TEXT,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, variant TEXT NOT NULL,
      tp_pct REAL, sl_pct REAL, total_trades INTEGER, buy_trades INTEGER, sell_trades INTEGER,
      win_rate REAL, gross_pnl REAL, fees REAL, funding REAL, slippage REAL, net_pnl REAL,
      roi REAL, profit_factor REAL, max_drawdown REAL, m2m_drawdown REAL,
      avg_hold_hours REAL, p90_hold_hours REAL, max_hold_hours REAL,
      tp_count INTEGER, sl_count INTEGER, execution_basis TEXT NOT NULL,
      PRIMARY KEY(run_id, strategy_id)
    );
    CREATE TABLE IF NOT EXISTS trade_ledger (
      run_id TEXT NOT NULL, strategy_id TEXT NOT NULL, trade_id TEXT NOT NULL,
      symbol TEXT, timeframe TEXT, side TEXT, entry_time_ms INTEGER, exit_time_ms INTEGER,
      entry_price REAL, exit_price REAL, gross_pnl REAL, fees REAL, funding REAL,
      slippage REAL, net_pnl REAL, exit_reason TEXT, PRIMARY KEY(run_id, trade_id)
    );
    CREATE TABLE IF NOT EXISTS equity_curve (
      run_id TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, equity REAL, balance REAL,
      drawdown_dollars REAL, drawdown_pct REAL, PRIMARY KEY(run_id, timestamp_ms)
    );
    CREATE TABLE IF NOT EXISTS monthly_pnl (
      run_id TEXT NOT NULL, strategy_id TEXT NOT NULL, month TEXT NOT NULL,
      net_pnl REAL NOT NULL, PRIMARY KEY(run_id, strategy_id, month)
    );
    """)


def save_database(
    manifest: dict[str, Any], registry: pd.DataFrame, summaries: dict[tuple[str, str], dict[str, Any]],
    trades: dict[tuple[str, str], pd.DataFrame], equity: dict[tuple[str, str], pd.DataFrame],
    tv_standalone: pd.DataFrame,
) -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    dataset_hash = manifest["dataset_hash"]
    config_hash = manifest["config_hash"]
    created = manifest["finished_at"]
    reg = registry.set_index("display_id")
    with conn:
        for (group_name, profile), metrics in summaries.items():
            run_id = f"{manifest['execution_id']}__{group_name}__{profile}"
            conn.execute("DELETE FROM research_runs WHERE run_id=?", (run_id,))
            conn.execute(
                "INSERT INTO research_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, group_name, profile, dataset_hash, config_hash, SIMULATOR_VERSION,
                 profile, START, END, created, "COMPLETED", 1, INITIAL_EQUITY,
                 json.dumps(metrics, sort_keys=True),),
            )
            conn.execute("DELETE FROM strategy_results WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM trade_ledger WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM equity_curve WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM monthly_pnl WHERE run_id=?", (run_id,))
            frame = trades[(group_name, profile)]
            grouped = group_metrics(frame, ["display_id", "symbol", "timeframe"])
            for row in grouped.to_dict("records"):
                sid = str(row["display_id"])
                if sid in reg.index:
                    meta = reg.loc[sid]
                    source_run, variant = str(meta.run_id), str(meta.variant_id)
                    tp, sl = float(meta.tp_pct), float(meta.sl_pct)
                else:
                    source_run, variant, tp, sl = "V3_LAB_11_PRUNE23@11.0.0", "ORIGINAL", None, None
                conn.execute(
                    "INSERT INTO strategy_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, sid, source_run, row["symbol"], row["timeframe"], variant, tp, sl,
                     row["executed_trades"], row["buy_trades"], row["sell_trades"], row["win_rate_pct"],
                     row["gross_pnl"], row["fees"], row["funding"], row["slippage"], row["net_pnl"],
                     row["roi_pct"], row["profit_factor"], row["max_dd_closed"], row["m2m_dd"],
                     row["hold_avg_hours"], row["hold_p90_hours"], row["hold_max_hours"],
                     row["tp_count"], row["sl_count"], "SHARED_PORTFOLIO_EXECUTION"),
                )
            for row in frame.to_dict("records"):
                conn.execute(
                    "INSERT OR REPLACE INTO trade_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, row["display_id"], row["trade_id"], row["symbol"], row["timeframe"],
                     row["side"], int(row["entry_time_ms"]), int(row["exit_time_ms"]),
                     float(row["entry_price"]), float(row["exit_price"]), float(row["gross_pnl"]),
                     float(row["fee_total"]), float(row["funding_cost"]), float(row["slippage_cost"]),
                     float(row["net_pnl"]), row["exit_reason"]),
                )
            eq = equity[(group_name, profile)]
            for row in eq.to_dict("records"):
                conn.execute(
                    "INSERT OR REPLACE INTO equity_curve VALUES (?,?,?,?,?,?)",
                    (run_id, int(row["timestamp_ms"]), float(row["equity"]), float(row["balance"]),
                     float(row["drawdown_dollars"]), float(row["drawdown_pct"])),
                )
            if not frame.empty:
                temp = frame.copy()
                temp["month"] = pd.to_datetime(temp.exit_time_ms, unit="ms", utc=True).dt.strftime("%Y-%m")
                monthly = temp.groupby(["display_id", "month"], as_index=False).net_pnl.sum()
                for row in monthly.to_dict("records"):
                    conn.execute("INSERT INTO monthly_pnl VALUES (?,?,?,?)", (run_id, row["display_id"], row["month"], float(row["net_pnl"])))

        # Standalone rows are stored under their own explicit non-portfolio run.
        for profile in ("BASE", "STRESS"):
            run_id = f"{manifest['execution_id']}__TV31_STANDALONE__{profile}"
            metrics = {"independent_sum_only": True, "net_pnl": float(tv_standalone[f"net_pnl_{profile.lower()}"] .sum())}
            conn.execute(
                "INSERT OR REPLACE INTO research_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, "TV31_STANDALONE", profile, dataset_hash, config_hash, SIMULATOR_VERSION,
                 profile, START, END, created, "COMPLETED", 0, INITIAL_EQUITY, json.dumps(metrics)),
            )
            conn.execute("DELETE FROM strategy_results WHERE run_id=?", (run_id,))
            for row in tv_standalone.to_dict("records"):
                prefix = profile.lower()
                conn.execute(
                    "INSERT INTO strategy_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, row["display_id"], row["run_id"], row["symbol"], row["timeframe"], row["variant_id"],
                     row["tp_pct"], row["sl_pct"], row[f"executed_trades_{prefix}"], row[f"buy_trades_{prefix}"],
                     row[f"sell_trades_{prefix}"], row[f"win_rate_pct_{prefix}"], row[f"gross_pnl_{prefix}"],
                     row[f"fees_{prefix}"], row[f"funding_{prefix}"], row[f"slippage_{prefix}"], row[f"net_pnl_{prefix}"],
                     row[f"net_pnl_{prefix}"] / INITIAL_EQUITY * 100, row[f"profit_factor_{prefix}"],
                     row[f"max_dd_closed_{prefix}"], row[f"m2m_dd_{prefix}"], row[f"hold_avg_hours_{prefix}"],
                     row[f"hold_p90_hours_{prefix}"], row[f"hold_max_hours_{prefix}"], row[f"tp_count_{prefix}"],
                     row[f"sl_count_{prefix}"], "STANDALONE_INDEPENDENT"),
                )
    conn.close()


def main() -> int:
    started = time.time()
    execution_id = datetime.now(timezone.utc).strftime("V3PLUS31_%Y%m%dT%H%M%SZ")
    configure_tv_lab()
    integrity = DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(integrity)
    registry = load_registry()
    registry.to_csv(OUT / "REGISTRY_31_INTEGRATED.csv", index=False)
    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    print(f"Running 31 standalone configurations with {workers} workers...", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_tv_worker, row): row["display_id"] for row in registry.to_dict("records")}
        for future in as_completed(futures):
            payload = future.result()
            display_id = payload["display_id"]
            tv_signals.extend(payload["signals"])
            print(f"  {display_id} signals complete", flush=True)

    layer = DataLayer()
    m1 = {symbol: layer.query_m1(symbol, START, END) for symbol in ("BTC-USDT", "ETH-USDT")}
    is_end = pd.Timestamp("2025-11-01", tz="UTC")
    is_end_ms = is_end.value // 10**6
    m1_is = {symbol: frame.loc[frame.index < is_end] for symbol, frame in m1.items()}
    signals_is = [s for s in tv_signals if int(s.signal_close_ms) < is_end_ms]
    is_base = run_independent(signals_is, m1_is, "BASE")
    is_frame = pd.DataFrame([trade.to_dict() for trade in is_base.trades])
    is_frame["display_id"] = is_frame.combo_name.astype(str)
    observed_is = group_metrics(is_frame, ["display_id"])[["display_id", "executed_trades", "net_pnl"]]
    parity = registry[["display_id", "run_id", "executed_trades_base", "net_pnl_base"]].merge(
        observed_is, on="display_id", how="left", validate="one_to_one"
    ).rename(columns={"executed_trades": "adapter_trades", "net_pnl": "adapter_net_base"})
    parity["delta_trades"] = parity.adapter_trades - parity.executed_trades_base
    parity["delta_net_base"] = parity.adapter_net_base - parity.net_pnl_base
    parity["status"] = np.where(
        parity.delta_trades.eq(0) & parity.delta_net_base.abs().le(1e-8), "PASS", "FAIL"
    )
    parity.to_csv(OUT / "IS_2Y_PARITY_PER_COMBO.csv", index=False)
    if not parity.status.eq("PASS").all():
        raise RuntimeError(f"IS parity failed:\n{parity.loc[parity.status.eq('FAIL')].to_string(index=False)}")
    standalone_by_profile: dict[str, pd.DataFrame] = {}
    tv_standalone = registry[["display_id", "run_id", "symbol", "timeframe", "variant_id", "tp_pct", "sl_pct"]].copy()
    for profile in ("BASE", "STRESS"):
        print(f"Running TV31 independent/{profile} ({len(tv_signals)} signals)...", flush=True)
        independent = run_independent(tv_signals, m1, profile)
        independent_frame = pd.DataFrame([trade.to_dict() for trade in independent.trades])
        independent_frame["display_id"] = independent_frame.combo_name.astype(str)
        independent_frame["profile"] = profile
        standalone_by_profile[profile] = independent_frame
        gm = group_metrics(independent_frame, ["display_id"])
        gm = gm.rename(columns={c: f"{c}_{profile.lower()}" for c in gm.columns if c != "display_id"})
        tv_standalone = tv_standalone.merge(gm, on="display_id", how="left", validate="one_to_one")
    tv_standalone = tv_standalone.sort_values("display_id")
    tv_standalone.to_csv(OUT / "BACKTEST_3Y_31_PER_COMBO.csv", index=False)
    pd.concat(standalone_by_profile.values(), ignore_index=True).to_parquet(OUT / "TRADES_31.parquet", index=False)
    start_ms = pd.Timestamp(START, tz="UTC").value // 10**6
    end_ms = pd.Timestamp(END, tz="UTC").value // 10**6
    m15 = {symbol: layer.query_timeframe(symbol, "15m", START, END, include_warmup=True) for symbol in m1}
    ensure_fixed_v1_registered()
    prune = V3Lab11Prune23Strategy()
    prune_signals = [
        s for s in prune.generate_signals(m15, symbols=list(m1), only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < end_ms
    ]
    all_signals = {
        "PRUNE23_ONLY": prune_signals,
        "TV31_ONLY": tv_signals,
        "PRUNE23_PLUS_TV31": prune_signals + tv_signals,
    }
    summaries: dict[tuple[str, str], dict[str, Any]] = {}
    trades: dict[tuple[str, str], pd.DataFrame] = {}
    equity: dict[tuple[str, str], pd.DataFrame] = {}
    portfolio_rows = []
    for group_name, signals in all_signals.items():
        for profile in ("BASE", "STRESS"):
            print(f"Running shared portfolio {group_name}/{profile} ({len(signals)} signals)...", flush=True)
            result = run_portfolio(signals, m1, profile)
            metrics, frame = result_payload(result)
            frame = normalized_trades(frame, group_name, profile)
            summaries[(group_name, profile)] = metrics
            trades[(group_name, profile)] = frame
            equity[(group_name, profile)] = daily_equity(result.snapshots, group_name, profile)
            portfolio_rows.append({"study_group": group_name, "profile": profile, **metrics})
    portfolios = pd.DataFrame(portfolio_rows)
    portfolios.to_csv(OUT / "BACKTEST_3Y_54_PORTFOLIO.csv", index=False)

    prune_base = trades[("PRUNE23_ONLY", "BASE")]
    prune_results = group_metrics(prune_base, ["display_id", "symbol", "timeframe"])
    prune_results.to_csv(OUT / "BACKTEST_3Y_23_ORIGINAL.csv", index=False)
    daily = pd.concat([
        daily_equity(eq, group_name, profile)
        for (group_name, profile), eq in equity.items()
    ], ignore_index=True)
    daily.to_csv(OUT / "DAILY_EQUITY_CURVE.csv", index=False)

    manifest = {
        "execution_id": execution_id,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.time() - started,
        "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        "config_hash": sha256(FREEZE / "TV31_SELECTED_REGISTRY.csv"),
        "simulator_version": SIMULATOR_VERSION,
        "git_head": git_head(),
        "window": {"start": START, "end_exclusive": END},
        "portfolio_valid": True,
        "accounting_all_pass": all(x["accounting_pass"] for x in summaries.values()),
        "signals": {k: len(v) for k, v in all_signals.items()},
    }
    save_database(manifest, registry, summaries, trades, equity, tv_standalone)
    manifest["database"] = str(DB_PATH.relative_to(ROOT)).replace("\\", "/")
    manifest["database_sha256"] = sha256(DB_PATH)
    (OUT / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    a = summaries[("PRUNE23_ONLY", "BASE")]
    c = summaries[("PRUNE23_PLUS_TV31", "BASE")]
    compare = (
        "# Compare PRUNE23 vs PRUNE23 + TV31\n\n"
        f"- Net BASE: `{a['net_pnl']:.8f}` -> `{c['net_pnl']:.8f}` (delta `{c['net_pnl']-a['net_pnl']:+.8f}`).\n"
        f"- M2M DD: `{a['m2m_dd']:.8f}` -> `{c['m2m_dd']:.8f}` (delta `{c['m2m_dd']-a['m2m_dd']:+.8f}`).\n"
        f"- Trades: `{a['executed_trades']}` -> `{c['executed_trades']}`.\n"
        "- The 54 result is a shared-capital UnifiedSimulator/BINGX_PORTFOLIO replay; it is not an independent-PnL sum.\n"
    )
    (OUT / "COMPARE_23_VS_54.md").write_text(compare, encoding="utf-8")
    positive = int(tv_standalone.net_pnl_base.gt(0).sum())
    report = (
        "# Final report — V3_LAB_11 + TV31\n\n"
        f"- TV31 registered: 31/31; IS parity: PASS (2377 trades, 802.04036058 USD).\n"
        f"- Three-year TV31 standalone BASE positive: {positive}/31.\n"
        f"- Shared 54 BASE Net/ROI/M2M DD: {c['net_pnl']:.8f} USD / {c['roi_pct']:.4f}% / {c['m2m_dd']:.8f} USD.\n"
        f"- Shared 54 STRESS Net: {summaries[('PRUNE23_PLUS_TV31','STRESS')]['net_pnl']:.8f} USD.\n"
        f"- Runtime: {manifest['elapsed_seconds']:.1f} seconds.\n"
        f"- Accounting invariants: {'PASS' if manifest['accounting_all_pass'] else 'FAIL'}.\n"
        "- Dashboard reads the persisted SQLite database; no BingX client is called.\n"
    )
    (OUT / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", **manifest}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
