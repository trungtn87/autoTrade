"""External backtest ledger importer into RunStore (`source='EXTERNAL'`)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.runstore.hashing import canonical_json, sha256_file, sha256_text
from autotrade_v3.runstore.store import RunStore, StoreSaveResult

REQUIRED_EXTERNAL_COLS = (
    "symbol",
    "combo_name",
    "side",
    "signal_close_ms",
    "entry_time_ms",
    "entry_price",
    "exit_time_ms",
    "exit_price",
    "net_pnl",
)

OPTIONAL_EXTERNAL_COLS = (
    "leg_id",
    "trade_id",
    "combo_id",
    "timeframe",
    "exit_reason",
    "gross_pnl",
    "gross_pnl_raw",
    "fee_entry",
    "fee_exit",
    "fee_total",
    "spread_cost",
    "slippage_cost",
    "funding_cost",
    "entry_px_raw",
    "entry_px_fill",
    "exit_px_trigger",
    "exit_px_fill",
    "ambiguity_type",
)


def import_external_ledger(
    ledger_path: str | Path,
    mapping_path: str | Path,
    *,
    store: RunStore | None = None,
    initial_equity: float = 1000.0,
    run_label: str = "external",
    code_sha: str = "EXTERNAL",
) -> StoreSaveResult:
    """Import an external backtest ledger (CSV/Parquet) into RunStore using *mapping_path*."""
    lp = Path(ledger_path)
    mp = Path(mapping_path)
    if not lp.exists():
        raise FileNotFoundError(f"External ledger not found: {lp}")
    if not mp.exists():
        raise FileNotFoundError(f"Mapping file not found: {mp}")

    mapping: dict[str, str] = json.loads(mp.read_text(encoding="utf-8"))
    raw_df = pd.read_parquet(lp) if lp.suffix.lower() == ".parquet" else pd.read_csv(lp)

    # mapping maps canonical_col -> source_col
    rename_map = {src: canon for canon, src in mapping.items() if src in raw_df.columns}
    df = raw_df.rename(columns=rename_map).copy()

    missing_required = [c for c in REQUIRED_EXTERNAL_COLS if c not in df.columns]
    if missing_required:
        raise ValueError(f"External ledger missing required mapped columns: {missing_required}")

    missing_optional = [c for c in OPTIONAL_EXTERNAL_COLS if c not in df.columns]
    for col in missing_optional:
        df[col] = None

    # Build canonical signal_id from symbol, combo_name, side, signal_close_ms (and timeframe if present)
    tf_series = df["timeframe"].fillna("15m").astype(str) if "timeframe" in df.columns else pd.Series(["15m"] * len(df))
    sids: list[str] = []
    leg_ids: list[str] = []
    trade_ids: list[str] = []
    for i, row in df.reset_index(drop=True).iterrows():
        sym_clean = str(row["symbol"]).replace("-", "")
        tf_val = str(tf_series.iloc[i])
        sid = f"SIG_{sym_clean}_{row['combo_name']}_{tf_val}_{row['side']}_{int(row['signal_close_ms'])}"
        sids.append(sid)
        leg_ids.append(str(row["leg_id"]) if pd.notna(row["leg_id"]) and str(row["leg_id"]) else f"{sid}#L{i + 1}")
        trade_ids.append(str(row["trade_id"]) if pd.notna(row["trade_id"]) and str(row["trade_id"]) else f"EXT_T{i + 1}")

    df["signal_id"] = sids
    df["leg_id"] = leg_ids
    df["trade_id"] = trade_ids

    signals_df = (
        df[["signal_id", "symbol", "combo_id", "combo_name", "timeframe", "side", "signal_close_ms", "entry_price"]]
        .drop_duplicates("signal_id")
        .reset_index(drop=True)
    )
    decisions_df = pd.DataFrame(
        {
            "signal_id": signals_df["signal_id"],
            "symbol": signals_df["symbol"],
            "status": ["ACCEPTED"] * len(signals_df),
            "reason": [""] * len(signals_df),
        }
    )
    events_df = pd.DataFrame(columns=["signal_id", "event_id", "event_type"])

    net_total = float(df["net_pnl"].astype(float).sum()) if len(df) else 0.0
    analysis: dict[str, Any] = {
        "source": "EXTERNAL",
        "accounting": {
            "total_signals": int(len(signals_df)),
            "accepted_entries": int(len(signals_df)),
            "total_legs": int(len(df)),
        },
        "pnl": {
            "net_pnl": round(net_total, 4),
            "net_pnl_unrounded": net_total,
            "roi_pct": round(net_total / float(initial_equity) * 100.0, 2) if initial_equity else 0.0,
        },
    }

    ledger_sha = sha256_file(lp)
    map_sha = sha256_text(canonical_json(mapping))
    run_id = "R_EXT_" + sha256_text(f"{ledger_sha}:{map_sha}:{run_label}")[:12]

    manifest = {
        "run_id": run_id,
        "source": "EXTERNAL",
        "external_ledger_path": str(lp.resolve()),
        "external_ledger_sha256": ledger_sha,
        "external_mapping_sha256": map_sha,
        "missing_columns": missing_optional,
        "execution_mode": "EXTERNAL",
        "strategy_id": str(run_label),
        "strategy_version": "EXTERNAL",
        "initial_equity": float(initial_equity),
        "symbols": sorted(df["symbol"].astype(str).unique().tolist()),
        "total_signals": int(len(signals_df)),
        "accepted_entries": int(len(signals_df)),
        "total_legs": int(len(df)),
        "code_sha": code_sha,
        "code_dirty": False,
        "validation_status": "EXTERNAL_UNVERIFIED",
    }

    target_store = store or RunStore()
    return target_store.save_run(
        run_id=run_id,
        manifest=manifest,
        signals_df=signals_df,
        decisions_df=decisions_df,
        legs_df=df,
        events_df=events_df,
        analysis=analysis,
        validation={"overall_status": "EXTERNAL_UNVERIFIED", "note": "Imported from external ledger"},
        code_dirty=False,
    )
