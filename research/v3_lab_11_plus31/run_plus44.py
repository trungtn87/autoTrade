from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import run_backtest as core


ROOT = core.ROOT
LAB = core.LAB
OUT = core.OUT / "plus44"
OUT.mkdir(parents=True, exist_ok=True)

REMOVED = {"TV01", "TV02", "TV05", "TV06", "TV11", "TV18", "TV19", "TV25", "TV26", "TV29"}
KEPT = {
    "TV03", "TV04", "TV07", "TV08", "TV09", "TV10", "TV12", "TV13", "TV14", "TV15", "TV16",
    "TV17", "TV20", "TV21", "TV22", "TV23", "TV24", "TV27", "TV28", "TV30", "TV31",
}


class PythonFleetMonitor:
    """Samples aggregate local Python CPU/RAM while the local replay is running."""

    def __init__(self, interval: float = 3.0) -> None:
        self.interval = interval
        self.samples: list[dict[str, float]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _sample() -> dict[str, float]:
        command = (
            "Get-Process python -ErrorAction SilentlyContinue | "
            "Select-Object Id,CPU,WorkingSet64 | ConvertTo-Json -Compress"
        )
        try:
            raw = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", command], text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).strip()
            parsed = json.loads(raw) if raw else []
            rows = parsed if isinstance(parsed, list) else [parsed]
            return {
                "timestamp": time.time(),
                "cpu_seconds": sum(float(row.get("CPU") or 0.0) for row in rows),
                "working_set_bytes": sum(float(row.get("WorkingSet64") or 0.0) for row in rows),
                "processes": float(len(rows)),
            }
        except Exception:
            return {"timestamp": time.time(), "cpu_seconds": 0.0, "working_set_bytes": 0.0, "processes": 0.0}

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self._sample())
            self._stop.wait(self.interval)

    def start(self) -> None:
        self.samples.append(self._sample())
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=self.interval + 2)
        self.samples.append(self._sample())
        valid = [row for row in self.samples if row["processes"] > 0]
        if len(valid) < 2:
            return {"sample_count": len(valid), "peak_working_set_mb": None, "avg_cpu_pct_total_capacity": None, "peak_cpu_pct_total_capacity": None}
        logical = max(1, os.cpu_count() or 1)
        cpu_rates = []
        for left, right in zip(valid, valid[1:]):
            elapsed = right["timestamp"] - left["timestamp"]
            if elapsed > 0:
                cpu_rates.append(max(0.0, right["cpu_seconds"] - left["cpu_seconds"]) / elapsed / logical * 100.0)
        elapsed = valid[-1]["timestamp"] - valid[0]["timestamp"]
        avg_cpu = max(0.0, valid[-1]["cpu_seconds"] - valid[0]["cpu_seconds"]) / elapsed / logical * 100.0 if elapsed > 0 else 0.0
        return {
            "scope": "aggregate local python.exe fleet; dashboard/API stopped during replay",
            "logical_cpu_count": logical,
            "sample_count": len(valid),
            "peak_python_processes": int(max(row["processes"] for row in valid)),
            "peak_working_set_mb": max(row["working_set_bytes"] for row in valid) / 1_048_576,
            "avg_cpu_pct_total_capacity": avg_cpu,
            "peak_cpu_pct_total_capacity": max(cpu_rates, default=0.0),
        }


def config_hash(registry: pd.DataFrame) -> str:
    payload = {
        "study": "V3_LAB_11_PLUS44",
        "kept": sorted(KEPT),
        "removed": sorted(REMOVED),
        "registry": registry[["display_id", "run_id", "strategy_source_sha256", "tp_pct", "sl_pct"]].to_dict("records"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def peak_concurrent(frame: pd.DataFrame) -> int:
    events: list[tuple[int, int]] = []
    for row in frame.itertuples():
        events.append((int(row.entry_time_ms), 1))
        events.append((int(row.exit_time_ms), -1))
    events.sort(key=lambda item: (item[0], item[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def latest_metrics(conn: sqlite3.Connection, group_name: str, profile: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT run_id,metrics_json FROM research_runs WHERE study_group=? AND profile=? ORDER BY created_at DESC LIMIT 1",
        (group_name, profile),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Missing comparison run {group_name}/{profile}")
    metrics = json.loads(row[1])
    if "peak_concurrent_legs" not in metrics:
        ledger = pd.read_sql_query(
            "SELECT entry_time_ms,exit_time_ms FROM trade_ledger WHERE run_id=?", conn, params=(row[0],)
        )
        metrics["peak_concurrent_legs"] = peak_concurrent(ledger)
    return metrics


def main() -> int:
    started = time.time()
    execution_id = datetime.now(timezone.utc).strftime("V3PLUS44_%Y%m%dT%H%M%SZ")
    monitor = PythonFleetMonitor()
    monitor.start()
    core.configure_tv_lab()
    integrity = core.DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(integrity)

    full = core.load_registry()
    assert set(full.display_id) == KEPT | REMOVED
    registry = full.loc[full.display_id.isin(KEPT)].sort_values("display_id").reset_index(drop=True)
    assert len(registry) == 21 and registry.display_id.nunique() == 21
    registry.to_csv(OUT / "TV21_SELECTED_REGISTRY.csv", index=False)

    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    print(f"Generating 21 frozen TV configurations with {workers} workers...", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(core._tv_worker, row): row["display_id"] for row in registry.to_dict("records")}
        for future in as_completed(futures):
            payload = future.result()
            tv_signals.extend(payload["signals"])
            print(f"  {payload['display_id']} signals complete", flush=True)

    layer = core.DataLayer()
    m1 = {symbol: layer.query_m1(symbol, core.START, core.END) for symbol in ("BTC-USDT", "ETH-USDT")}

    # Quantitative IS parity for the retained 21 before opening the three-year replay.
    is_end = pd.Timestamp("2025-11-01", tz="UTC")
    is_end_ms = is_end.value // 10**6
    m1_is = {symbol: frame.loc[frame.index < is_end] for symbol, frame in m1.items()}
    is_signals = [signal for signal in tv_signals if int(signal.signal_close_ms) < is_end_ms]
    is_result = core.run_independent(is_signals, m1_is, "BASE")
    is_frame = pd.DataFrame([trade.to_dict() for trade in is_result.trades])
    is_frame["display_id"] = is_frame.combo_name.astype(str)
    observed = core.group_metrics(is_frame, ["display_id"])[["display_id", "executed_trades", "net_pnl"]]
    parity = registry[["display_id", "run_id", "executed_trades_base", "net_pnl_base"]].merge(
        observed, on="display_id", how="left", validate="one_to_one"
    ).rename(columns={"executed_trades": "adapter_trades", "net_pnl": "adapter_net_base"})
    parity["delta_trades"] = parity.adapter_trades - parity.executed_trades_base
    parity["delta_net_base"] = parity.adapter_net_base - parity.net_pnl_base
    parity["status"] = np.where(parity.delta_trades.eq(0) & parity.delta_net_base.abs().le(1e-8), "PASS", "FAIL")
    parity.to_csv(OUT / "TV21_IS_PARITY.csv", index=False)
    if not parity.status.eq("PASS").all():
        raise RuntimeError(f"TV21 IS parity failure:\n{parity.loc[parity.status.eq('FAIL')].to_string(index=False)}")

    standalone = registry[["display_id", "run_id", "symbol", "timeframe", "variant_id", "tp_pct", "sl_pct"]].copy()
    standalone_trades: dict[str, pd.DataFrame] = {}
    for profile in ("BASE", "STRESS"):
        print(f"Running TV21 standalone/{profile} ({len(tv_signals)} signals)...", flush=True)
        result = core.run_independent(tv_signals, m1, profile)
        frame = pd.DataFrame([trade.to_dict() for trade in result.trades])
        frame["display_id"] = frame.combo_name.astype(str)
        frame["profile"] = profile
        standalone_trades[profile] = frame
        metrics = core.group_metrics(frame, ["display_id"])
        metrics = metrics.rename(columns={column: f"{column}_{profile.lower()}" for column in metrics if column != "display_id"})
        standalone = standalone.merge(metrics, on="display_id", how="left", validate="one_to_one")
    standalone.to_csv(OUT / "BACKTEST_3Y_TV21_STANDALONE.csv", index=False)
    pd.concat(standalone_trades.values(), ignore_index=True).to_parquet(OUT / "TRADES_TV21_STANDALONE.parquet", index=False)

    start_ms = pd.Timestamp(core.START, tz="UTC").value // 10**6
    end_ms = pd.Timestamp(core.END, tz="UTC").value // 10**6
    m15 = {symbol: layer.query_timeframe(symbol, "15m", core.START, core.END, include_warmup=True) for symbol in m1}
    core.ensure_fixed_v1_registered()
    prune = core.V3Lab11Prune23Strategy()
    prune_signals = [
        signal for signal in prune.generate_signals(m15, symbols=list(m1), only_approved=True, only_warmup_ready=True)
        if start_ms <= int(signal.signal_close_ms) + 1 < end_ms
    ]

    signal_sets = {
        "PRUNE23_ONLY": prune_signals,
        "TV21_ONLY": tv_signals,
        "PRUNE23_PLUS_TV21": prune_signals + tv_signals,
    }
    summaries: dict[tuple[str, str], dict[str, Any]] = {}
    trades: dict[tuple[str, str], pd.DataFrame] = {}
    equity: dict[tuple[str, str], pd.DataFrame] = {}
    portfolio_rows: list[dict[str, Any]] = []
    for group_name, signals in signal_sets.items():
        for profile in ("BASE", "STRESS"):
            print(f"Running shared portfolio {group_name}/{profile} ({len(signals)} signals)...", flush=True)
            result = core.run_portfolio(signals, m1, profile)
            metrics, frame = core.result_payload(result)
            frame = core.normalized_trades(frame, group_name, profile)
            metrics["peak_concurrent_legs"] = peak_concurrent(frame)
            summaries[(group_name, profile)] = metrics
            trades[(group_name, profile)] = frame
            equity[(group_name, profile)] = core.daily_equity(result.snapshots, group_name, profile)
            portfolio_rows.append({"study_group": group_name, "profile": profile, **metrics})

    finished_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "execution_id": execution_id,
        "study": "V3_LAB_11_PLUS44",
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "finished_at": finished_at,
        "elapsed_seconds": time.time() - started,
        "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        "config_hash": config_hash(registry),
        "simulator_version": core.SIMULATOR_VERSION,
        "git_head": core.git_head(),
        "window": {"start": core.START, "end_exclusive": core.END},
        "initial_equity": core.INITIAL_EQUITY,
        "kept_tv": sorted(KEPT),
        "removed_tv": sorted(REMOVED),
        "portfolio_valid": True,
        "accounting_all_pass": all(item["accounting_pass"] for item in summaries.values()),
        "signals": {name: len(items) for name, items in signal_sets.items()},
    }
    core.save_database(manifest, registry, summaries, trades, equity, standalone, standalone_group="TV21_STANDALONE")

    portfolios = pd.DataFrame(portfolio_rows)
    portfolios.to_csv(OUT / "BACKTEST_3Y_PLUS44_PORTFOLIOS.csv", index=False)
    pd.concat(equity.values(), ignore_index=True).to_csv(OUT / "DAILY_EQUITY_PLUS44.csv", index=False)

    # Explicitly distinguish independent strategy PnL from allocation inside TV21 shared portfolio.
    tv21_base = core.group_metrics(trades[("TV21_ONLY", "BASE")], ["display_id"])[["display_id", "net_pnl", "executed_trades"]]
    attribution = standalone[["display_id", "net_pnl_base", "executed_trades_base"]].merge(
        tv21_base, on="display_id", how="left", validate="one_to_one"
    ).rename(columns={
        "net_pnl_base": "standalone_net_base", "executed_trades_base": "standalone_trades",
        "net_pnl": "portfolio_allocated_net_base", "executed_trades": "portfolio_executed_trades",
    })
    attribution["allocation_minus_standalone"] = attribution.portfolio_allocated_net_base - attribution.standalone_net_base
    attribution["label"] = "STANDALONE_INDEPENDENT vs SHARED_PORTFOLIO_ALLOCATION"
    attribution.to_csv(OUT / "TV21_STANDALONE_VS_PORTFOLIO.csv", index=False)

    resource_metrics = monitor.stop()
    manifest["resource_metrics"] = resource_metrics
    manifest["elapsed_seconds"] = time.time() - started
    manifest["database"] = str(core.DB_PATH.relative_to(ROOT)).replace("\\", "/")

    with sqlite3.connect(core.DB_PATH) as conn:
        comparisons: dict[str, dict[str, Any]] = {}
        for label, group_name in (
            ("PRUNE23", "PRUNE23_ONLY"),
            ("PLUS44", "PRUNE23_PLUS_TV21"),
            ("PLUS54", "PRUNE23_PLUS_TV31"),
        ):
            comparisons[label] = {
                profile: latest_metrics(conn, group_name, profile) for profile in ("BASE", "STRESS")
            }

    manifest["database_sha256"] = core.sha256(core.DB_PATH)
    (OUT / "RUN_MANIFEST_PLUS44.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    lines = [
        "# V3_LAB_11 PLUS44 - final comparison", "",
        "All portfolio values below come from actual shared-capital `UnifiedSimulator/BINGX_PORTFOLIO` replays.", "",
        "| Portfolio | Net BASE | Net STRESS | ROI BASE | ROI STRESS | M2M DD BASE | M2M DD STRESS | Trades BASE | Trades STRESS | Y1/Y2/Y3 BASE | Y1/Y2/Y3 STRESS | Peak legs BASE/STRESS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|",
    ]
    for label in ("PRUNE23", "PLUS44", "PLUS54"):
        base, stress = comparisons[label]["BASE"], comparisons[label]["STRESS"]
        lines.append(
            f"| {label} | {base['net_pnl']:.8f} | {stress['net_pnl']:.8f} | {base['roi_pct']:.4f}% | "
            f"{stress['roi_pct']:.4f}% | {base['m2m_dd']:.4f} | {stress['m2m_dd']:.4f} | "
            f"{base['executed_trades']:,} | {stress['executed_trades']:,} | "
            f"{base['p1_net']:.4f} / {base['p2_net']:.4f} / {base['p3_net']:.4f} | "
            f"{stress['p1_net']:.4f} / {stress['p2_net']:.4f} / {stress['p3_net']:.4f} | "
            f"{base.get('peak_concurrent_legs', 'n/a')} / {stress.get('peak_concurrent_legs', 'n/a')} |"
        )
    lines += [
        "", "## Resource usage", "",
        f"- Runtime: {manifest['elapsed_seconds']:.1f} seconds.",
        f"- Peak aggregate Python working set: {resource_metrics.get('peak_working_set_mb', 0):.1f} MB.",
        f"- Average CPU utilization over total logical capacity: {resource_metrics.get('avg_cpu_pct_total_capacity', 0):.2f}%.",
        f"- Peak sampled CPU utilization over total logical capacity: {resource_metrics.get('peak_cpu_pct_total_capacity', 0):.2f}%.",
        "", "## Accounting and attribution", "",
        f"- Accounting invariants: {'PASS' if manifest['accounting_all_pass'] else 'FAIL'}.",
        "- `TV21_STANDALONE` is explicitly labeled independent; `TV21_ONLY` and `PRUNE23_PLUS_TV21` are shared portfolio executions.",
        "- See `TV21_STANDALONE_VS_PORTFOLIO.csv` for per-code allocation differences.",
        "- The previous PLUS54 database rows and artifacts were preserved.",
    ]
    (OUT / "FINAL_REPORT_PLUS44.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", **manifest}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
