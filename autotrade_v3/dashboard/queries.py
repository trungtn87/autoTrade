"""Pure read-only query layer for AutoTrade V3 Research Lab Dashboard.

Every number, table, and series rendered on the 7 dashboard screens is returned
by a single function in this module. All DuckDB connections are opened with
``read_only=True`` and no function here writes to the RunStore or catalog.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import load_m1_from_catalog, resample_m1_to_timeframe
from autotrade_v3.runstore.diff import diff_runs
from autotrade_v3.sim.costs import load_cost_profiles
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry, compute_param_diff, parse_strategy_ref

DEFAULT_STORE_DIR = Path("runs/lab/store")
DEFAULT_CATALOG_PATH = Path("catalog/research.duckdb")
DEFAULT_QA_PATH = Path("catalog/v3_m1_qa.json")
DEFAULT_NOTES_DB = Path("runs/lab/notes.duckdb")
DEFAULT_ANNOTATIONS_DB = Path("runs/lab/annotations/annotations.duckdb")
DEFAULT_ACTION_LOG = Path("runs/lab/dashboard_actions.log")
DEFAULT_DATASET_VERIFY_LOG = Path("runs/lab/dataset_verify_last.json")

REQUIRED_RUN_ARTIFACTS = (
    "run_manifest.json",
    "signals.parquet",
    "signal_decisions.parquet",
    "legs.parquet",
    "events.parquet",
    "equity.parquet",
    "validation.json",
)


class MissingArtifactError(FileNotFoundError):
    """Raised when a required run artifact is missing from the RunStore."""


def connect_duckdb_readonly(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB database strictly in read-only mode."""
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"DuckDB file not found: {p}")
    return duckdb.connect(str(p), read_only=True)


def _resolve_run_dir(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> Path:
    sdir = Path(store_dir)
    p = sdir / str(run_id)
    if p.exists() and p.is_dir():
        return p
    direct = Path(run_id)
    if direct.exists() and direct.is_dir() and (direct / "run_manifest.json").exists():
        return direct
    raise FileNotFoundError(f"Run '{run_id}' not found in store: {sdir}")


def _require_file(run_dir: Path, filename: str) -> Path:
    p = run_dir / filename
    if not p.exists():
        raise MissingArtifactError(f"Missing required artifact '{filename}' in {run_dir}")
    return p


def _load_json_required(run_dir: Path, filename: str) -> dict[str, Any]:
    p = _require_file(run_dir, filename)
    return dict(json.loads(p.read_text(encoding="utf-8")))


def _load_parquet_required(run_dir: Path, filename: str) -> pd.DataFrame:
    p = _require_file(run_dir, filename)
    return pd.read_parquet(p)


def _effective_validation_status(
    manifest: dict[str, Any],
    validation: dict[str, Any] | None,
) -> tuple[str, bool]:
    """Return (validation_status, is_synthetic) using validation.json as the single source of truth."""
    is_synthetic = bool(manifest.get("smoke_test", False) or manifest.get("synthetic", False))
    if validation is not None and validation.get("overall_status"):
        raw_status = str(validation["overall_status"])
    else:
        raw_status = "UNVERIFIED"

    if bool(manifest.get("code_dirty", False)) and raw_status == "VERIFIED":
        raw_status = "UNVERIFIED_DIRTY"
    if is_synthetic and raw_status == "VERIFIED":
        raw_status = "SYNTHETIC"
    return raw_status, is_synthetic


def _calc_pf_ratio(wins: float, losses: float) -> tuple[float | str, float | str]:
    if losses > 1e-9:
        ratio = wins / losses
        return round(ratio, 2), ratio
    if wins > 1e-9:
        return "UNDEFINED_NO_LOSS", "UNDEFINED_NO_LOSS"
    return 0.0, 0.0


def _compute_four_pf_from_df(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "net_pf_trade": 0.0,
            "net_pf_trade_unrounded": 0.0,
            "net_pf_leg": 0.0,
            "net_pf_leg_unrounded": 0.0,
            "gross_pf_trade": 0.0,
            "gross_pf_trade_unrounded": 0.0,
            "gross_pf_leg": 0.0,
            "gross_pf_leg_unrounded": 0.0,
        }
    l_net = df["net_pnl"].astype(float)
    l_gross = df["gross_pnl"].astype(float)
    l_net_w = float(l_net[l_net > 0].sum())
    l_net_l = float(abs(l_net[l_net < 0].sum()))
    l_gross_w = float(l_gross[l_gross > 0].sum())
    l_gross_l = float(abs(l_gross[l_gross < 0].sum()))

    t_net = df.groupby("trade_id")["net_pnl"].sum().astype(float)
    t_gross = df.groupby("trade_id")["gross_pnl"].sum().astype(float)
    t_net_w = float(t_net[t_net > 0].sum())
    t_net_l = float(abs(t_net[t_net < 0].sum()))
    t_gross_w = float(t_gross[t_gross > 0].sum())
    t_gross_l = float(abs(t_gross[t_gross < 0].sum()))

    npt_r, npt_u = _calc_pf_ratio(t_net_w, t_net_l)
    npl_r, npl_u = _calc_pf_ratio(l_net_w, l_net_l)
    gpt_r, gpt_u = _calc_pf_ratio(t_gross_w, t_gross_l)
    gpl_r, gpl_u = _calc_pf_ratio(l_gross_w, l_gross_l)
    return {
        "net_pf_trade": npt_r,
        "net_pf_trade_unrounded": npt_u,
        "net_pf_leg": npl_r,
        "net_pf_leg_unrounded": npl_u,
        "gross_pf_trade": gpt_r,
        "gross_pf_trade_unrounded": gpt_u,
        "gross_pf_leg": gpl_r,
        "gross_pf_leg_unrounded": gpl_u,
    }


def _compute_closed_trade_drawdown(legs_df: pd.DataFrame, initial_equity: float) -> tuple[float, float]:
    if legs_df.empty:
        return 0.0, 0.0
    ordered = legs_df.sort_values(["exit_time_ms", "leg_id"], kind="mergesort")
    cum = ordered["net_pnl"].astype(float).cumsum()
    peak = cum.cummax().clip(lower=0.0)
    dd = peak - cum
    max_dd = float(dd.max()) if len(dd) else 0.0
    if max_dd <= 0.0:
        return 0.0, 0.0
    idx = int(dd.idxmax())
    ref_cap = float(initial_equity) + float(peak.loc[idx])
    max_dd_pct = (max_dd / ref_cap * 100.0) if ref_cap > 0 else 0.0
    return max_dd, max_dd_pct


def _compute_m2m_drawdown(equity_df: pd.DataFrame) -> tuple[float | None, float | None]:
    if equity_df.empty or "equity" not in equity_df.columns:
        return None, None
    eq = equity_df["equity"].astype(float)
    peak = eq.cummax()
    dd = peak - eq
    max_dd = float(dd.max()) if len(dd) else 0.0
    if max_dd <= 0.0:
        return 0.0, 0.0
    idx = int(dd.idxmax())
    pk_val = float(peak.loc[idx])
    max_dd_pct = (max_dd / pk_val * 100.0) if pk_val > 0 else 0.0
    return max_dd, max_dd_pct


# ---------------------------------------------------------------------------
# Screen 1: Datasets
# ---------------------------------------------------------------------------


def get_datasets_overview(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    qa_path: str | Path = DEFAULT_QA_PATH,
    store_dir: str | Path = DEFAULT_STORE_DIR,
) -> dict[str, Any]:
    """Return dataset metadata from catalog/manifest without modifying files."""
    cat_p = Path(catalog_path)
    qa_p = Path(qa_path)
    if not cat_p.exists() and not qa_p.exists():
        return {"datasets": [], "has_data": False}

    dl = DataLayer(catalog_path=cat_p, qa_path=qa_p)
    manifest = dl.get_manifest()
    gate_a = get_last_gate_a_status(store_dir=store_dir, qa_path=qa_p)
    row = {
        "name": manifest.name,
        "start_utc": manifest.start_utc,
        "end_utc": manifest.end_utc,
        "symbols": manifest.symbols,
        "total_candles": manifest.total_candles,
        "symbol_candle_counts": manifest.symbol_candle_counts,
        "partition_count": manifest.partition_count,
        "dataset_hash": manifest.data_manifest_hash,
        "status": manifest.status,
        "is_valid": manifest.is_valid,
        "gate_a1_status": gate_a.get("A1", "NOT_RUN"),
        "gate_a2_status": gate_a.get("A2", "NOT_RUN"),
        "gate_a_source": gate_a.get("source", ""),
    }
    return {"datasets": [row], "has_data": manifest.total_candles > 0}


def get_dataset_provider_breakdown(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
) -> pd.DataFrame:
    """Return per-symbol, per-provider candle counts and percentages (read-only)."""
    cat_p = Path(catalog_path)
    if not cat_p.exists():
        return pd.DataFrame(columns=["symbol", "provider", "candles", "pct"])
    con = connect_duckdb_readonly(cat_p)
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        table_name = "v3_m1" if "v3_m1" in tables else ("candles" if "candles" in tables else "")
        if not table_name:
            return pd.DataFrame(columns=["symbol", "provider", "candles", "pct"])
        cols = {r[1] for r in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()}
        if "provider" in cols:
            df = con.execute(
                f"SELECT symbol, COALESCE(provider, 'unknown') AS provider, COUNT(*) AS candles "
                f"FROM {table_name} GROUP BY symbol, provider ORDER BY symbol, candles DESC"
            ).df()
        else:
            df = con.execute(
                f"SELECT symbol, 'unknown' AS provider, COUNT(*) AS candles "
                f"FROM {table_name} GROUP BY symbol ORDER BY symbol"
            ).df()
    finally:
        con.close()

    if df.empty:
        df["pct"] = pd.Series(dtype="float64")
        return df
    totals = df.groupby("symbol")["candles"].transform("sum")
    df["pct"] = (df["candles"] / totals * 100.0).round(2)
    return df


def get_last_gate_a_status(
    store_dir: str | Path = DEFAULT_STORE_DIR,
    qa_path: str | Path = DEFAULT_QA_PATH,
    verify_log_path: str | Path = DEFAULT_DATASET_VERIFY_LOG,
) -> dict[str, Any]:
    """Return most recent Gate A1 and A2 statuses from verify log or latest store validation.json."""
    v_p = Path(verify_log_path)
    if v_p.exists():
        try:
            data = json.loads(v_p.read_text(encoding="utf-8"))
            return {
                "A1": str(data.get("A1", "PASS" if data.get("passed") else "FAIL")),
                "A2": str(data.get("A2", "PASS" if data.get("passed") else "FAIL")),
                "timestamp_utc": str(data.get("timestamp_utc", "")),
                "source": str(v_p),
            }
        except Exception:
            pass

    sdir = Path(store_dir)
    if sdir.exists():
        best_ts = ""
        best_gates: dict[str, Any] = {}
        best_src = ""
        for sub in sorted(sdir.iterdir()):
            val_p = sub / "validation.json"
            man_p = sub / "run_manifest.json"
            if sub.is_dir() and val_p.exists():
                try:
                    val = json.loads(val_p.read_text(encoding="utf-8"))
                    gates = {str(g.get("gate_id", "")): str(g.get("status", "")) for g in val.get("gates", [])}
                    ts = ""
                    if man_p.exists():
                        ts = str(json.loads(man_p.read_text(encoding="utf-8")).get("created_at_utc", ""))
                    if "A1" in gates or "A2" in gates:
                        if ts >= best_ts:
                            best_ts = ts
                            best_gates = gates
                            best_src = f"{sub.name}/validation.json"
                except Exception:
                    continue
        if best_gates:
            return {
                "A1": best_gates.get("A1", "UNKNOWN"),
                "A2": best_gates.get("A2", "UNKNOWN"),
                "timestamp_utc": best_ts,
                "source": best_src,
            }

    return {"A1": "NOT_RUN", "A2": "NOT_RUN", "timestamp_utc": "", "source": str(qa_path)}


# ---------------------------------------------------------------------------
# Screen 2: Strategies
# ---------------------------------------------------------------------------


def list_strategies_info(params_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """List registered strategy versions with 3 hashes, parent, and lock state."""
    p_dir = Path(params_dir) if params_dir is not None else None
    keys = StrategyCoreRegistry.list_keys(params_dir=p_dir)
    rows: list[dict[str, Any]] = []
    for ref in keys:
        strat = StrategyCoreRegistry.get(ref, params_dir=p_dir)
        sp = strat.spec()
        sid, ver = parse_strategy_ref(sp.strategy_id, sp.version)
        is_locked = (sid, ver) == ("20FINAL", "1")
        parent = None
        if not is_locked:
            base_dir = p_dir or (Path(__file__).resolve().parents[1] / "strategy_core" / "strategies" / "final20" / "params")
            slug = f"{sid.lower()}_v{ver}"
            cand = base_dir / slug / "params.json"
            if cand.exists():
                raw = json.loads(cand.read_text(encoding="utf-8"))
                parent = raw.get("parent")
        rows.append(
            {
                "ref": f"{sid}@{ver}",
                "strategy_id": sid,
                "version": ver,
                "parent": parent,
                "is_locked": is_locked,
                "combo_count": len(sp.combos),
                "signal_spec_hash": sp.signal_spec_hash,
                "exit_spec_hash": sp.exit_spec_hash,
                "spec_hash": sp.spec_hash,
            }
        )
    return rows


def get_strategy_detail(strategy_ref: str, params_dir: str | Path | None = None) -> dict[str, Any]:
    """Return full strategy spec and combo table for a strategy version."""
    p_dir = Path(params_dir) if params_dir is not None else None
    strat = StrategyCoreRegistry.get(strategy_ref, params_dir=p_dir)
    sp = strat.spec()
    sid, ver = parse_strategy_ref(sp.strategy_id, sp.version)
    is_locked = (sid, ver) == ("20FINAL", "1")
    combos_rows: list[dict[str, Any]] = []
    for c in sp.combos:
        ep = c.exit_policy
        combos_rows.append(
            {
                "combo_key": c.combo_key,
                "combo_id": c.combo_id,
                "symbol": c.symbol,
                "combo_name": c.combo_name,
                "timeframe": c.native_timeframe,
                "entry_variant": c.entry_variant,
                "entry": json.dumps(c.entry, sort_keys=True),
                "filters": c.layer2,
                "tp_pct": ep.tp_pct,
                "sl_pct": ep.sl_pct,
                "trailing_act_pct": ep.trailing_act_pct,
                "trailing_cb_pct": ep.trailing_cb_pct,
                "max_bars_held": ep.max_bars_held,
                "leg_weights": json.dumps(list(ep.leg_weights)),
                "exit_policy_hash": ep.get_policy_hash(),
            }
        )
    return {
        "ref": f"{sid}@{ver}",
        "strategy_id": sid,
        "version": ver,
        "is_locked": is_locked,
        "warmup_bars": sp.warmup_bars,
        "timeframes": list(sp.timeframes),
        "signal_spec_hash": sp.signal_spec_hash,
        "exit_spec_hash": sp.exit_spec_hash,
        "spec_hash": sp.spec_hash,
        "combos_df": pd.DataFrame(combos_rows),
        "raw_dict": sp.to_dict(),
    }


def get_strategy_param_diff(
    strategy_ref: str,
    params_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return parameter diff vs parent for a cloned strategy (None for baseline)."""
    sid, ver = parse_strategy_ref(strategy_ref)
    if (sid, ver) == ("20FINAL", "1"):
        return None
    p_dir = Path(params_dir) if params_dir is not None else (
        Path(__file__).resolve().parents[1] / "strategy_core" / "strategies" / "final20" / "params"
    )
    slug = f"{sid.lower()}_v{ver}"
    diff_path = p_dir / slug / "param_diff.json"
    if diff_path.exists():
        return dict(json.loads(diff_path.read_text(encoding="utf-8")))
    params_path = p_dir / slug / "params.json"
    if not params_path.exists():
        return None
    child_data = json.loads(params_path.read_text(encoding="utf-8"))
    parent_ref = child_data.get("parent")
    if not parent_ref:
        return None
    parent_strat = StrategyCoreRegistry.get(str(parent_ref), params_dir=p_dir)
    return compute_param_diff(parent_strat.spec().to_dict(), child_data)


# ---------------------------------------------------------------------------
# Screen 3: New Run
# ---------------------------------------------------------------------------


def list_cost_profiles_for_ui() -> list[dict[str, Any]]:
    """List execution cost profiles and whether each is selectable for real M1 data."""
    profiles = load_cost_profiles()
    out: list[dict[str, Any]] = []
    for name, prof in profiles.items():
        out.append(
            {
                "name": prof.name,
                "status": prof.status,
                "source": prof.source,
                "profile_hash": prof.profile_hash,
                "selectable_for_real_data": prof.status == "CONFIRMED",
            }
        )
    return out


def get_new_run_options(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    params_dir: str | Path | None = None,
    annotations_db: str | Path = DEFAULT_ANNOTATIONS_DB,
) -> dict[str, Any]:
    """Return selectable options for the New Run screen."""
    strategies = list_strategies_info(params_dir=params_dir)
    cost_profiles = list_cost_profiles_for_ui()
    annotation_sources: list[dict[str, Any]] = []
    ann_p = Path(annotations_db)
    if ann_p.exists():
        con = connect_duckdb_readonly(ann_p)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            if "annotations" in tables:
                rows = con.execute(
                    "SELECT DISTINCT source, BOOL_AND(as_of_verified) AS all_verified "
                    "FROM annotations GROUP BY source ORDER BY source"
                ).fetchall()
                annotation_sources = [{"source": str(r[0]), "as_of_verified": bool(r[1])} for r in rows]
        finally:
            con.close()

    return {
        "strategies": [s["ref"] for s in strategies],
        "modes": ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"],
        "ambiguity_policies": ["CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH", "LEGACY"],
        "symbols": ["BTC-USDT", "ETH-USDT"],
        "cost_profiles": cost_profiles,
        "annotation_sources": annotation_sources,
        "catalog_exists": Path(catalog_path).exists(),
    }


def get_action_log_entries(
    log_path: str | Path = DEFAULT_ACTION_LOG,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read recent dashboard action log entries."""
    p = Path(log_path)
    if not p.exists():
        return []
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    entries: list[dict[str, Any]] = []
    for ln in reversed(lines[-limit:]):
        try:
            entries.append(json.loads(ln))
        except Exception:
            entries.append({"raw": ln})
    return entries


# ---------------------------------------------------------------------------
# Screen 4: Run Detail
# ---------------------------------------------------------------------------


def list_store_runs(store_dir: str | Path = DEFAULT_STORE_DIR) -> list[dict[str, Any]]:
    """List runs from RunStore index.duckdb (read-only) or scan directories read-only."""
    sdir = Path(store_dir)
    if not sdir.exists():
        return []

    db_path = sdir / "index.duckdb"
    rows: list[dict[str, Any]] = []
    if db_path.exists():
        con = connect_duckdb_readonly(db_path)
        try:
            cur = con.execute("SELECT * FROM runs ORDER BY created_at_utc DESC, run_id ASC")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
    else:
        for sub in sorted(sdir.iterdir()):
            man_p = sub / "run_manifest.json"
            if sub.is_dir() and man_p.exists():
                m = json.loads(man_p.read_text(encoding="utf-8"))
                rows.append(
                    {
                        "run_id": str(m.get("run_id", sub.name)),
                        "created_at_utc": str(m.get("created_at_utc", "")),
                        "execution_mode": str(m.get("execution_mode", "")),
                        "strategy_id": str(m.get("strategy_id", "")),
                        "strategy_version": str(m.get("strategy_version", "")),
                        "cost_profile": str(m.get("cost_profile", "")),
                        "ambiguity_policy": str(m.get("ambiguity_policy", "")),
                        "validation_status": "UNVERIFIED",
                        "source": str(m.get("source", "V3")),
                        "dataset_hash": str(m.get("dataset_hash", "")),
                        "spec_hash": str(m.get("spec_hash", "")),
                        "code_sha": str(m.get("code_sha", "")),
                        "code_dirty": bool(m.get("code_dirty", False)),
                        "total_signals": int(m.get("total_signals", 0)),
                        "accepted_entries": int(m.get("accepted_entries", 0)),
                        "total_legs": int(m.get("total_legs", 0)),
                        "net_pnl": 0.0,
                        "roi_pct": 0.0,
                        "run_dir": str(sub.resolve()),
                    }
                )

    for r in rows:
        rdir = sdir / str(r["run_id"])
        val_p = rdir / "validation.json"
        man_p = rdir / "run_manifest.json"
        val = json.loads(val_p.read_text(encoding="utf-8")) if val_p.exists() else None
        man = json.loads(man_p.read_text(encoding="utf-8")) if man_p.exists() else r
        status, is_syn = _effective_validation_status(man, val)
        r["validation_status"] = status
        r["is_synthetic"] = is_syn
    return rows


def get_run_summary(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> dict[str, Any]:
    """Compute Run Detail summary cards directly from RunStore ledger and artifacts."""
    rdir = _resolve_run_dir(run_id, store_dir)
    manifest = _load_json_required(rdir, "run_manifest.json")
    validation = _load_json_required(rdir, "validation.json")
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    decisions_df = _load_parquet_required(rdir, "signal_decisions.parquet")
    _require_file(rdir, "signals.parquet")
    equity_df = _load_parquet_required(rdir, "equity.parquet")

    mode = str(manifest.get("execution_mode", ""))
    initial_equity = float(manifest.get("initial_equity", 1000.0))
    validation_status, is_synthetic = _effective_validation_status(manifest, validation)

    total_legs = len(legs_df)
    total_entries = int(legs_df["trade_id"].nunique()) if total_legs > 0 else 0
    gross_pnl = float(legs_df["gross_pnl"].astype(float).sum()) if total_legs > 0 else 0.0
    total_fees = float(legs_df["fee_total"].astype(float).sum()) if total_legs > 0 else 0.0
    net_pnl = float(legs_df["net_pnl"].astype(float).sum()) if total_legs > 0 else 0.0
    pf = _compute_four_pf_from_df(legs_df)

    # Trade-level & leg-level win rate
    if total_legs > 0:
        leg_wins = int((legs_df["net_pnl"].astype(float) > 0).sum())
        leg_losses = int((legs_df["net_pnl"].astype(float) < 0).sum())
        leg_win_rate_pct = leg_wins / total_legs * 100.0
        trade_net = legs_df.groupby("trade_id")["net_pnl"].sum().astype(float)
        trade_wins = int((trade_net > 0).sum())
        trade_losses = int((trade_net < 0).sum())
        trade_win_rate_pct = trade_wins / len(trade_net) * 100.0 if len(trade_net) > 0 else 0.0
    else:
        leg_wins = leg_losses = trade_wins = trade_losses = 0
        leg_win_rate_pct = trade_win_rate_pct = 0.0

    # ROI attribution (4.4: Mode A/B are reference on nominal capital, never equated to Mode C)
    roi_pct = (net_pnl / initial_equity * 100.0) if initial_equity > 0 else 0.0
    if mode == "BINGX_PORTFOLIO":
        roi_label = "Portfolio ROI (%)"
        roi_note = "Finite-capital portfolio ROI on initial equity."
    else:
        roi_label = "ROI tham chiếu trên vốn danh nghĩa (%)"
        roi_note = "tham chiếu trên vốn danh nghĩa — không so sánh trực tiếp với ROI Mode C."

    # Drawdown
    closed_dd_dollars, closed_dd_pct = _compute_closed_trade_drawdown(legs_df, initial_equity)
    if mode == "BINGX_PORTFOLIO":
        m2m_dd_dollars, m2m_dd_pct = _compute_m2m_drawdown(equity_df)
        m2m_status = "AVAILABLE" if m2m_dd_dollars is not None else "MISSING"
    else:
        m2m_dd_dollars, m2m_dd_pct = None, None
        m2m_status = "NOT_APPLICABLE"

    # Decision counts from signal_decisions.parquet
    dec_counts: dict[str, int] = {}
    if not decisions_df.empty and "status" in decisions_df.columns:
        vc = decisions_df["status"].astype(str).value_counts().to_dict()
        dec_counts = {str(k): int(v) for k, v in vc.items()}
    total_signals = len(decisions_df)

    return {
        "run_id": str(manifest.get("run_id", rdir.name)),
        "strategy_id": str(manifest.get("strategy_id", "")),
        "strategy_version": str(manifest.get("strategy_version", "")),
        "execution_mode": mode,
        "cost_profile": str(manifest.get("cost_profile", "")),
        "ambiguity_policy": str(manifest.get("ambiguity_policy", "")),
        "validation_status": validation_status,
        "is_synthetic": is_synthetic,
        "is_real_data": bool(validation.get("is_real_data", not is_synthetic)),
        "source": str(manifest.get("source", "V3")),
        "dataset_hash": str(manifest.get("dataset_hash", "")),
        "spec_hash": str(manifest.get("spec_hash", "")),
        "time_window": manifest.get("time_window", {}),
        "symbols": manifest.get("symbols", []),
        "initial_equity": initial_equity,
        "total_signals": total_signals,
        "decision_counts": dec_counts,
        "accepted_entries": total_entries,
        "total_legs": total_legs,
        "gross_pnl": gross_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "roi_pct": roi_pct,
        "roi_label": roi_label,
        "roi_note": roi_note,
        "net_pf_trade": pf["net_pf_trade"],
        "net_pf_leg": pf["net_pf_leg"],
        "gross_pf_trade": pf["gross_pf_trade"],
        "gross_pf_leg": pf["gross_pf_leg"],
        "trade_wins": trade_wins,
        "trade_losses": trade_losses,
        "trade_win_rate_pct": trade_win_rate_pct,
        "leg_wins": leg_wins,
        "leg_losses": leg_losses,
        "leg_win_rate_pct": leg_win_rate_pct,
        "closed_trade_max_drawdown_dollars": closed_dd_dollars,
        "closed_trade_max_drawdown_pct": closed_dd_pct,
        "m2m_max_drawdown_dollars": m2m_dd_dollars,
        "m2m_max_drawdown_pct": m2m_dd_pct,
        "m2m_status": m2m_status,
    }


def get_run_equity_curve(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> pd.DataFrame:
    """Return equity & drawdown time series from equity.parquet."""
    rdir = _resolve_run_dir(run_id, store_dir)
    eq = _load_parquet_required(rdir, "equity.parquet").copy()
    if eq.empty:
        return pd.DataFrame(columns=["time_ms", "timestamp_dt", "equity", "balance", "drawdown_dollars", "drawdown_pct", "equity_kind"])
    eq["equity"] = eq["equity"].astype(float)
    peak = eq["equity"].cummax()
    eq["drawdown_dollars"] = peak - eq["equity"]
    eq["drawdown_pct"] = (eq["drawdown_dollars"] / peak.where(peak > 0, 1.0) * 100.0).fillna(0.0)
    eq["timestamp_dt"] = pd.to_datetime(eq["time_ms"].astype("int64"), unit="ms", utc=True)
    return eq


def get_run_symbol_breakdown(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> pd.DataFrame:
    """Compute per-symbol metrics directly from legs.parquet and signal_decisions.parquet."""
    rdir = _resolve_run_dir(run_id, store_dir)
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    dec_df = _load_parquet_required(rdir, "signal_decisions.parquet")
    if legs_df.empty:
        return pd.DataFrame(
            columns=["symbol", "signals", "entries", "legs", "trade_win_rate_pct", "leg_win_rate_pct", "gross_pnl", "fees", "net_pnl", "net_pf_trade", "net_pf_leg"]
        )
    rows: list[dict[str, Any]] = []
    for sym, grp in legs_df.groupby("symbol", sort=True):
        pf = _compute_four_pf_from_df(grp)
        t_net = grp.groupby("trade_id")["net_pnl"].sum().astype(float)
        twr = float((t_net > 0).sum()) / len(t_net) * 100.0 if len(t_net) > 0 else 0.0
        lwr = float((grp["net_pnl"].astype(float) > 0).sum()) / len(grp) * 100.0 if len(grp) > 0 else 0.0
        sig_cnt = int((dec_df["symbol"].astype(str) == str(sym)).sum()) if not dec_df.empty and "symbol" in dec_df.columns else int(grp["signal_id"].nunique())
        rows.append(
            {
                "symbol": str(sym),
                "signals": sig_cnt,
                "entries": int(grp["trade_id"].nunique()),
                "legs": len(grp),
                "trade_win_rate_pct": round(twr, 2),
                "leg_win_rate_pct": round(lwr, 2),
                "gross_pnl": float(grp["gross_pnl"].astype(float).sum()),
                "fees": float(grp["fee_total"].astype(float).sum()),
                "net_pnl": float(grp["net_pnl"].astype(float).sum()),
                "net_pf_trade": pf["net_pf_trade"],
                "net_pf_leg": pf["net_pf_leg"],
            }
        )
    return pd.DataFrame(rows)


def get_run_combo_breakdown(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> pd.DataFrame:
    """Compute combo table strictly keyed by (symbol, combo_name), never combo_id alone."""
    rdir = _resolve_run_dir(run_id, store_dir)
    manifest = _load_json_required(rdir, "run_manifest.json")
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    dec_df = _load_parquet_required(rdir, "signal_decisions.parquet")
    mode = str(manifest.get("execution_mode", ""))
    if legs_df.empty:
        return pd.DataFrame(
            columns=["combo_key", "symbol", "combo_name", "timeframe", "signals", "entries", "legs", "leg_win_rate_pct", "gross_pnl", "fees", "net_pnl", "pnl_own_entry", "net_pf_trade", "net_pf_leg"]
        )

    df = legs_df.copy()
    df["combo_key"] = df["symbol"].astype(str) + "/" + df["combo_name"].astype(str)
    sig_counts: dict[str, int] = {}
    if not dec_df.empty and "symbol" in dec_df.columns and "combo_name" in dec_df.columns:
        ck_series = dec_df["symbol"].astype(str) + "/" + dec_df["combo_name"].astype(str)
        sig_counts = {str(k): int(v) for k, v in ck_series.value_counts().items()}

    rows: list[dict[str, Any]] = []
    for (sym, cname), grp in df.groupby(["symbol", "combo_name"], sort=True):
        ckey = f"{sym}/{cname}"
        pf = _compute_four_pf_from_df(grp)
        own_col = grp["pnl_own_entry"].astype(float) if "pnl_own_entry" in grp.columns else grp["net_pnl"].astype(float)
        lwr = float((grp["net_pnl"].astype(float) > 0).sum()) / len(grp) * 100.0 if len(grp) > 0 else 0.0
        rows.append(
            {
                "combo_key": ckey,
                "symbol": str(sym),
                "combo_name": str(cname),
                "timeframe": str(grp["timeframe"].iloc[0]) if "timeframe" in grp.columns else "",
                "signals": sig_counts.get(ckey, int(grp["signal_id"].nunique()) if "signal_id" in grp.columns else int(grp["trade_id"].nunique())),
                "entries": int(grp["trade_id"].nunique()),
                "legs": len(grp),
                "leg_win_rate_pct": round(lwr, 2),
                "gross_pnl": float(grp["gross_pnl"].astype(float).sum()),
                "fees": float(grp["fee_total"].astype(float).sum()),
                "net_pnl": float(grp["net_pnl"].astype(float).sum()),
                "pnl_own_entry": float(own_col.sum()),
                "pnl_attribution_basis": "pnl_own_entry" if mode == "BINGX_PORTFOLIO" else "net_pnl",
                "net_pf_trade": pf["net_pf_trade"],
                "net_pf_leg": pf["net_pf_leg"],
            }
        )
    return pd.DataFrame(rows)


def get_run_monthly_breakdown(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> pd.DataFrame:
    """Compute monthly breakdown directly from legs.parquet."""
    rdir = _resolve_run_dir(run_id, store_dir)
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    if legs_df.empty:
        return pd.DataFrame(columns=["year_month", "entries", "legs", "leg_win_rate_pct", "gross_pnl", "fees", "net_pnl", "net_pf_trade", "net_pf_leg"])

    df = legs_df.copy()
    if "entry_time_dt" in df.columns:
        df["year_month"] = df["entry_time_dt"].astype(str).str.slice(0, 7)
    else:
        df["year_month"] = pd.to_datetime(df["entry_time_ms"].astype("int64"), unit="ms", utc=True).dt.strftime("%Y-%m")

    rows: list[dict[str, Any]] = []
    for ym, grp in df.groupby("year_month", sort=True):
        pf = _compute_four_pf_from_df(grp)
        lwr = float((grp["net_pnl"].astype(float) > 0).sum()) / len(grp) * 100.0 if len(grp) > 0 else 0.0
        rows.append(
            {
                "year_month": str(ym),
                "entries": int(grp["trade_id"].nunique()),
                "legs": len(grp),
                "leg_win_rate_pct": round(lwr, 2),
                "gross_pnl": float(grp["gross_pnl"].astype(float).sum()),
                "fees": float(grp["fee_total"].astype(float).sum()),
                "net_pnl": float(grp["net_pnl"].astype(float).sum()),
                "net_pf_trade": pf["net_pf_trade"],
                "net_pf_leg": pf["net_pf_leg"],
            }
        )
    return pd.DataFrame(rows)


def get_run_subwindows_breakdown(
    run_id: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    subwindows: list[tuple[str, str, str]] | None = None,
) -> pd.DataFrame:
    """Compute entry-time attributed subwindow breakdown directly from legs.parquet."""
    rdir = _resolve_run_dir(run_id, store_dir)
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    defs = subwindows or ResearchAnalyzer.DEFAULT_SUBWINDOWS
    rows: list[dict[str, Any]] = []
    for name, w_start, w_end in defs:
        s_ms = pd.Timestamp(w_start, tz="UTC").value // 1_000_000
        e_ms = pd.Timestamp(w_end, tz="UTC").value // 1_000_000
        if legs_df.empty:
            grp = legs_df
        else:
            et = legs_df["entry_time_ms"].astype("int64")
            grp = legs_df[(et >= s_ms) & (et < e_ms)]
        pf = _compute_four_pf_from_df(grp)
        lwr = float((grp["net_pnl"].astype(float) > 0).sum()) / len(grp) * 100.0 if len(grp) > 0 else 0.0
        rows.append(
            {
                "subwindow": name,
                "start": w_start,
                "end": w_end,
                "entries": int(grp["trade_id"].nunique()) if len(grp) > 0 else 0,
                "legs": len(grp),
                "leg_win_rate_pct": round(lwr, 2),
                "gross_pnl": float(grp["gross_pnl"].astype(float).sum()) if len(grp) > 0 else 0.0,
                "fees": float(grp["fee_total"].astype(float).sum()) if len(grp) > 0 else 0.0,
                "net_pnl": float(grp["net_pnl"].astype(float).sum()) if len(grp) > 0 else 0.0,
                "net_pf_trade": pf["net_pf_trade"],
                "net_pf_leg": pf["net_pf_leg"],
            }
        )
    return pd.DataFrame(rows)


def get_run_exit_reasons(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> pd.DataFrame:
    """Compute exit reason distribution directly from legs.parquet."""
    rdir = _resolve_run_dir(run_id, store_dir)
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    if legs_df.empty:
        return pd.DataFrame(columns=["exit_reason", "count", "pct_of_legs", "gross_pnl", "net_pnl"])
    total = len(legs_df)
    rows: list[dict[str, Any]] = []
    for reason, grp in legs_df.groupby("exit_reason", sort=True):
        rows.append(
            {
                "exit_reason": str(reason),
                "count": len(grp),
                "pct_of_legs": round(len(grp) / total * 100.0, 2),
                "gross_pnl": float(grp["gross_pnl"].astype(float).sum()),
                "net_pnl": float(grp["net_pnl"].astype(float).sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("count", ascending=False, kind="mergesort").reset_index(drop=True)


def get_run_cost_breakdown(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> dict[str, Any]:
    """Compute itemized execution cost components directly from legs.parquet."""
    rdir = _resolve_run_dir(run_id, store_dir)
    manifest = _load_json_required(rdir, "run_manifest.json")
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    if legs_df.empty:
        return {
            "cost_profile": str(manifest.get("cost_profile", "")),
            "gross_pnl_raw": 0.0,
            "fee_entry": 0.0,
            "fee_exit": 0.0,
            "spread_cost": 0.0,
            "slippage_cost": 0.0,
            "funding_cost": 0.0,
            "total_fees": 0.0,
            "net_pnl": 0.0,
        }

    def _col_sum(name: str, fallback: str | None = None) -> float:
        if name in legs_df.columns:
            return float(legs_df[name].astype(float).sum())
        if fallback and fallback in legs_df.columns:
            return float(legs_df[fallback].astype(float).sum())
        return 0.0

    return {
        "cost_profile": str(manifest.get("cost_profile", "")),
        "gross_pnl_raw": _col_sum("gross_pnl_raw", "gross_pnl"),
        "fee_entry": _col_sum("fee_entry"),
        "fee_exit": _col_sum("fee_exit"),
        "spread_cost": _col_sum("spread_cost"),
        "slippage_cost": _col_sum("slippage_cost"),
        "funding_cost": _col_sum("funding_cost"),
        "total_fees": _col_sum("fee_total"),
        "net_pnl": _col_sum("net_pnl"),
    }


def get_run_uncertainty(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> dict[str, Any] | None:
    """Load ambiguity uncertainty interval JSON if present for this run."""
    rdir = _resolve_run_dir(run_id, store_dir)
    for cand in (rdir / "uncertainty.json", rdir.parent.parent / f"uncertainty_{rdir.name}.json"):
        if cand.exists():
            return dict(json.loads(cand.read_text(encoding="utf-8")))
    return None


def get_run_legs_drilldown(
    run_id: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    *,
    symbol: str | None = None,
    combo_key: str | None = None,
    year_month: str | None = None,
    subwindow_start: str | None = None,
    subwindow_end: str | None = None,
    exit_reason: str | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    """Return matching leg rows and exact sum invariants for drill-down verification."""
    rdir = _resolve_run_dir(run_id, store_dir)
    df = _load_parquet_required(rdir, "legs.parquet").copy()
    if not df.empty:
        if symbol is not None:
            df = df[df["symbol"].astype(str) == str(symbol)]
        if combo_key is not None:
            ck = df["symbol"].astype(str) + "/" + df["combo_name"].astype(str)
            df = df[ck == str(combo_key)]
        if year_month is not None:
            ym = (
                df["entry_time_dt"].astype(str).str.slice(0, 7)
                if "entry_time_dt" in df.columns
                else pd.to_datetime(df["entry_time_ms"].astype("int64"), unit="ms", utc=True).dt.strftime("%Y-%m")
            )
            df = df[ym == str(year_month)]
        if subwindow_start is not None and subwindow_end is not None:
            s_ms = pd.Timestamp(subwindow_start, tz="UTC").value // 1_000_000
            e_ms = pd.Timestamp(subwindow_end, tz="UTC").value // 1_000_000
            et = df["entry_time_ms"].astype("int64")
            df = df[(et >= s_ms) & (et < e_ms)]
        if exit_reason is not None:
            df = df[df["exit_reason"].astype(str) == str(exit_reason)]
        if signal_id is not None:
            df = df[df["signal_id"].astype(str) == str(signal_id)]

    return {
        "legs_df": df.reset_index(drop=True),
        "leg_count": len(df),
        "entry_count": int(df["trade_id"].nunique()) if not df.empty else 0,
        "sum_gross_pnl": float(df["gross_pnl"].astype(float).sum()) if not df.empty else 0.0,
        "sum_fees": float(df["fee_total"].astype(float).sum()) if not df.empty else 0.0,
        "sum_net_pnl": float(df["net_pnl"].astype(float).sum()) if not df.empty else 0.0,
    }


def get_run_decisions_drilldown(
    run_id: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    *,
    status: str | None = None,
) -> pd.DataFrame:
    """Return signal_decisions rows optionally filtered by decision status."""
    rdir = _resolve_run_dir(run_id, store_dir)
    df = _load_parquet_required(rdir, "signal_decisions.parquet")
    if status is not None and not df.empty and "status" in df.columns:
        df = df[df["status"].astype(str) == str(status)]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Screen 5: Signal Explorer
# ---------------------------------------------------------------------------


def list_run_signals(
    run_id: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    *,
    signal_id_query: str | None = None,
    symbol: str | None = None,
    combo_key: str | None = None,
    status: str | None = None,
    ambiguous_only: bool = False,
) -> pd.DataFrame:
    """List all signals of a run joined with decision status and leg PnL summary."""
    rdir = _resolve_run_dir(run_id, store_dir)
    signals_df = _load_parquet_required(rdir, "signals.parquet").copy()
    dec_df = _load_parquet_required(rdir, "signal_decisions.parquet").copy()
    legs_df = _load_parquet_required(rdir, "legs.parquet").copy()

    if signals_df.empty:
        return pd.DataFrame(
            columns=["signal_id", "symbol", "combo_name", "timeframe", "side", "signal_close_dt", "status", "reason", "leg_count", "net_pnl", "ambiguity_flag"]
        )

    base = signals_df[["signal_id", "symbol", "combo_name", "timeframe", "side", "signal_close_ms", "signal_close_dt", "entry_price"]].copy()
    base["signal_id"] = base["signal_id"].astype(str)

    if not dec_df.empty:
        dec_sub = dec_df[["signal_id", "status", "reason"]].copy()
        dec_sub["signal_id"] = dec_sub["signal_id"].astype(str)
        dec_sub = dec_sub.drop_duplicates(subset=["signal_id"])
        base = base.merge(dec_sub, on="signal_id", how="left")
    else:
        base["status"] = "UNKNOWN"
        base["reason"] = ""

    if not legs_df.empty and "signal_id" in legs_df.columns:
        lg = legs_df.copy()
        lg["signal_id"] = lg["signal_id"].astype(str)
        lg["is_ambig"] = (
            lg.get("intrabar_ambiguous", pd.Series(False, index=lg.index)).fillna(False).astype(bool)
            | lg.get("intrabar_trailing_ambiguous", pd.Series(False, index=lg.index)).fillna(False).astype(bool)
        )
        agg = lg.groupby("signal_id", as_index=False).agg(
            leg_count=("leg_id", "count"),
            net_pnl=("net_pnl", "sum"),
            ambiguity_flag=("is_ambig", "any"),
        )
        base = base.merge(agg, on="signal_id", how="left")
    else:
        base["leg_count"] = 0
        base["net_pnl"] = 0.0
        base["ambiguity_flag"] = False

    base["leg_count"] = base["leg_count"].fillna(0).astype(int)
    base["net_pnl"] = base["net_pnl"].fillna(0.0).astype(float)
    base["ambiguity_flag"] = base["ambiguity_flag"].fillna(False).astype(bool)
    base["status"] = base["status"].fillna("UNKNOWN").astype(str)
    base["reason"] = base["reason"].fillna("").astype(str)
    base["combo_key"] = base["symbol"].astype(str) + "/" + base["combo_name"].astype(str)

    if signal_id_query:
        q = signal_id_query.strip().lower()
        base = base[base["signal_id"].str.lower().str.contains(q, regex=False)]
    if symbol:
        base = base[base["symbol"].astype(str) == str(symbol)]
    if combo_key:
        base = base[base["combo_key"] == str(combo_key)]
    if status:
        base = base[base["status"] == str(status)]
    if ambiguous_only:
        base = base[base["ambiguity_flag"]]

    return base.sort_values(["signal_close_ms", "signal_id"], kind="mergesort").reset_index(drop=True)


def _reconstruct_trailing_stop_series(
    m1_df: pd.DataFrame,
    *,
    side: str,
    entry_px: float,
    entry_time_ms: int,
    exit_time_ms: int,
    trailing_act_pct: float,
    trailing_cb_pct: float,
) -> pd.DataFrame:
    """Reconstruct trailing stop level over M1 bars from entry to exit."""
    if m1_df.empty or trailing_act_pct <= 0 or trailing_cb_pct <= 0:
        return pd.DataFrame(columns=["time_ms", "trailing_stop", "activated"])

    sub = m1_df[(m1_df["time_ms"] >= entry_time_ms) & (m1_df["time_ms"] <= exit_time_ms)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["time_ms", "trailing_stop", "activated"])

    is_long = str(side).upper() == "LONG"
    act_px = entry_px * (1.0 + trailing_act_pct) if is_long else entry_px * (1.0 - trailing_act_pct)
    activated = False
    peak = entry_px
    rows: list[dict[str, Any]] = []
    for _, bar in sub.iterrows():
        hi = float(bar["high"])
        lo = float(bar["low"])
        if is_long:
            if not activated and hi >= act_px:
                activated = True
                peak = hi
            elif activated and hi > peak:
                peak = hi
            stop_px = peak * (1.0 - trailing_cb_pct) if activated else None
        else:
            if not activated and lo <= act_px:
                activated = True
                peak = lo
            elif activated and lo < peak:
                peak = lo
            stop_px = peak * (1.0 + trailing_cb_pct) if activated else None
        rows.append(
            {
                "time_ms": int(bar["time_ms"]),
                "trailing_stop": stop_px,
                "activated": activated,
            }
        )
    return pd.DataFrame(rows)


def get_signal_detail_and_chart(
    run_id: str,
    signal_id: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
) -> dict[str, Any]:
    """Return full signal detail, legs, events, M15 candles, M1 path, and trailing stop series."""
    rdir = _resolve_run_dir(run_id, store_dir)
    signals_df = _load_parquet_required(rdir, "signals.parquet")
    dec_df = _load_parquet_required(rdir, "signal_decisions.parquet")
    legs_df = _load_parquet_required(rdir, "legs.parquet")
    events_df = _load_parquet_required(rdir, "events.parquet")

    sig_rows = signals_df[signals_df["signal_id"].astype(str) == str(signal_id)]
    if sig_rows.empty:
        raise KeyError(f"signal_id '{signal_id}' not found in {run_id}")
    sig = sig_rows.iloc[0].to_dict()

    dec_rows = dec_df[dec_df["signal_id"].astype(str) == str(signal_id)]
    dec = dec_rows.iloc[0].to_dict() if not dec_rows.empty else {}

    sig_legs = legs_df[legs_df["signal_id"].astype(str) == str(signal_id)].copy().reset_index(drop=True)
    sig_events = events_df[events_df["signal_id"].astype(str) == str(signal_id)].copy().reset_index(drop=True)

    symbol = str(sig["symbol"])
    side = str(sig["side"]).upper()
    sig_close_ms = int(sig["signal_close_ms"])

    if not sig_legs.empty:
        entry_time_ms = int(sig_legs["entry_time_ms"].min())
        exit_time_ms = int(sig_legs["exit_time_ms"].max())
        entry_price = float(sig_legs["entry_px_raw"].iloc[0] if "entry_px_raw" in sig_legs.columns else sig_legs["entry_price"].iloc[0])
        tp_pct = float(sig_legs["tp_pct"].iloc[0]) if "tp_pct" in sig_legs.columns else float(sig.get("tp_pct", 0.0))
        sl_pct = float(sig_legs["sl_pct"].iloc[0]) if "sl_pct" in sig_legs.columns else float(sig.get("sl_pct", 0.0))
        trailing_act_pct = float(sig_legs["trailing_act_pct"].max()) if "trailing_act_pct" in sig_legs.columns else 0.0
        trailing_cb_pct = float(sig_legs["trailing_cb_pct"].max()) if "trailing_cb_pct" in sig_legs.columns else 0.0
    else:
        entry_time_ms = int(dec.get("entry_time_ms") or sig_close_ms)
        exit_time_ms = sig_close_ms + 60 * 60_000
        entry_price = float(sig["entry_price"])
        tp_pct = float(sig.get("tp_pct", 0.0))
        sl_pct = float(sig.get("sl_pct", 0.0))
        trailing_act_pct = 0.0
        trailing_cb_pct = 0.0

    is_long = side == "LONG"
    tp_price = entry_price * (1.0 + tp_pct) if tp_pct > 0 and is_long else (entry_price * (1.0 - tp_pct) if tp_pct > 0 else None)
    sl_price = entry_price * (1.0 - sl_pct) if sl_pct > 0 and is_long else (entry_price * (1.0 + sl_pct) if sl_pct > 0 else None)
    trailing_act_price = (
        (entry_price * (1.0 + trailing_act_pct) if is_long else entry_price * (1.0 - trailing_act_pct))
        if trailing_act_pct > 0
        else None
    )

    # Load M1 candles read-only around the signal window (aligned to 15m buckets)
    m15_df = pd.DataFrame()
    m1_path_df = pd.DataFrame()
    trailing_series_df = pd.DataFrame()
    cat_p = Path(catalog_path)
    if cat_p.exists():
        tf15_ms = 15 * 60_000
        pad_before_ms = 24 * 60 * 60_000  # 24h before signal for M15 context
        pad_after_ms = 2 * 60 * 60_000
        q_start = ((min(sig_close_ms, entry_time_ms) - pad_before_ms) // tf15_ms) * tf15_ms
        q_end = (((max(sig_close_ms, exit_time_ms) + pad_after_ms) // tf15_ms) + 1) * tf15_ms
        m1_indexed = load_m1_from_catalog(
            cat_p,
            symbol,
            pd.to_datetime(q_start, unit="ms", utc=True),
            pd.to_datetime(q_end, unit="ms", utc=True),
        )
        if not m1_indexed.empty:
            m15_indexed = resample_m1_to_timeframe(m1_indexed, 15)
            m15_df = m15_indexed.reset_index()
            m15_df["time_ms"] = (m15_df["open_ms"].astype("int64") // 1_000_000) if "open_ms" in m15_df.columns else (m15_indexed.index.astype("int64") // 1_000_000)

            m1_raw = m1_indexed.reset_index()
            idx_col = m1_raw.columns[0]
            m1_raw["time_ms"] = m1_raw[idx_col].astype("int64") // 1_000_000
            m1_path_df = m1_raw[
                (m1_raw["time_ms"] >= entry_time_ms - 5 * 60_000)
                & (m1_raw["time_ms"] <= exit_time_ms + 5 * 60_000)
            ].copy().reset_index(drop=True)
            trailing_series_df = _reconstruct_trailing_stop_series(
                m1_raw,
                side=side,
                entry_px=entry_price,
                entry_time_ms=entry_time_ms,
                exit_time_ms=exit_time_ms,
                trailing_act_pct=trailing_act_pct,
                trailing_cb_pct=trailing_cb_pct,
            )

    ambig_types = []
    if not sig_legs.empty:
        for _, r in sig_legs.iterrows():
            at = str(r.get("ambiguity_type", "") or "").strip()
            if at and at.upper() != "NONE":
                ambig_types.append(at)
            elif bool(r.get("intrabar_ambiguous", False)) or bool(r.get("intrabar_trailing_ambiguous", False)):
                ambig_types.append("AMBIGUOUS")

    return {
        "signal": sig,
        "decision": dec,
        "legs_df": sig_legs,
        "events_df": sig_events,
        "levels": {
            "entry_price": entry_price,
            "entry_time_ms": entry_time_ms,
            "exit_time_ms": exit_time_ms,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "trailing_act_price": trailing_act_price,
            "trailing_act_pct": trailing_act_pct,
            "trailing_cb_pct": trailing_cb_pct,
        },
        "ambiguity_flags": sorted(set(ambig_types)),
        "m15_df": m15_df,
        "m1_path_df": m1_path_df,
        "trailing_series_df": trailing_series_df,
    }


# ---------------------------------------------------------------------------
# Screen 6: Compare
# ---------------------------------------------------------------------------


def compare_runs_summary(
    run_ids: list[str],
    store_dir: str | Path = DEFAULT_STORE_DIR,
) -> dict[str, Any]:
    """Compare 2 to 6 runs side by side with mismatch warnings."""
    summaries = [get_run_summary(rid, store_dir=store_dir) for rid in run_ids]
    warnings: list[str] = []
    if len(summaries) >= 2:
        datasets = {s["dataset_hash"] for s in summaries}
        windows = {json.dumps(s["time_window"], sort_keys=True) for s in summaries}
        costs = {s["cost_profile"] for s in summaries}
        modes = {s["execution_mode"] for s in summaries}
        if len(datasets) > 1:
            warnings.append("Runs differ in dataset_hash.")
        if len(windows) > 1:
            warnings.append("Runs differ in time_window.")
        if len(costs) > 1:
            warnings.append("Runs differ in cost_profile.")
        if len(modes) > 1:
            warnings.append("Runs differ in execution_mode (ROI Mode A/B is nominal reference only, not comparable to Mode C).")

    table_rows = [
        {
            "run_id": s["run_id"],
            "strategy": f"{s['strategy_id']}@{s['strategy_version']}",
            "execution_mode": s["execution_mode"],
            "cost_profile": s["cost_profile"],
            "ambiguity_policy": s["ambiguity_policy"],
            "validation_status": s["validation_status"],
            "total_signals": s["total_signals"],
            "accepted_entries": s["accepted_entries"],
            "total_legs": s["total_legs"],
            "net_pnl": s["net_pnl"],
            "gross_pnl": s["gross_pnl"],
            "total_fees": s["total_fees"],
            "net_pf_trade": s["net_pf_trade"],
            "net_pf_leg": s["net_pf_leg"],
            "trade_win_rate_pct": round(s["trade_win_rate_pct"], 2),
            "leg_win_rate_pct": round(s["leg_win_rate_pct"], 2),
            "closed_dd_dollars": s["closed_trade_max_drawdown_dollars"],
            "m2m_dd_dollars": s["m2m_max_drawdown_dollars"],
        }
        for s in summaries
    ]
    return {"summaries": summaries, "table_df": pd.DataFrame(table_rows), "warnings": warnings}


def get_compare_equity_curves(
    run_ids: list[str],
    store_dir: str | Path = DEFAULT_STORE_DIR,
) -> pd.DataFrame:
    """Return stacked equity curves for overlaid plotting."""
    frames: list[pd.DataFrame] = []
    for rid in run_ids:
        eq = get_run_equity_curve(rid, store_dir=store_dir)
        if not eq.empty:
            eq["run_id"] = rid
            frames.append(eq[["run_id", "time_ms", "timestamp_dt", "equity", "drawdown_dollars", "equity_kind"]])
    if not frames:
        return pd.DataFrame(columns=["run_id", "time_ms", "timestamp_dt", "equity", "drawdown_dollars", "equity_kind"])
    return pd.concat(frames, ignore_index=True)


def get_compare_combo_deltas(
    run_ids: list[str],
    store_dir: str | Path = DEFAULT_STORE_DIR,
) -> pd.DataFrame:
    """Compare combo net_pnl keyed by (symbol, combo_name) across selected runs."""
    if not run_ids:
        return pd.DataFrame()
    base_df = get_run_combo_breakdown(run_ids[0], store_dir=store_dir)[["combo_key", "symbol", "combo_name", "net_pnl"]].rename(
        columns={"net_pnl": f"net_pnl_{run_ids[0]}"}
    )
    merged = base_df
    for rid in run_ids[1:]:
        other = get_run_combo_breakdown(rid, store_dir=store_dir)[["combo_key", "symbol", "combo_name", "net_pnl"]].rename(
            columns={"net_pnl": f"net_pnl_{rid}"}
        )
        merged = merged.merge(other, on=["combo_key", "symbol", "combo_name"], how="outer")
        merged[f"delta_{rid}_vs_{run_ids[0]}"] = merged[f"net_pnl_{rid}"].fillna(0.0) - merged[f"net_pnl_{run_ids[0]}"].fillna(0.0)
    return merged.sort_values(["symbol", "combo_name"], kind="mergesort").reset_index(drop=True)


def get_compare_signal_diff(
    run_a: str,
    run_b: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
) -> dict[str, Any]:
    """Run in-memory per-signal diff between run_a and run_b without mutating the store."""
    dir_a = _resolve_run_dir(run_a, store_dir)
    dir_b = _resolve_run_dir(run_b, store_dir)
    res = diff_runs(dir_a, dir_b, out_dir=None)
    return {"summary": res.summary, "diff_df": res.diff_df}


# ---------------------------------------------------------------------------
# Screen 7: Research Log
# ---------------------------------------------------------------------------


def list_research_log(
    store_dir: str | Path = DEFAULT_STORE_DIR,
    notes_db_path: str | Path = DEFAULT_NOTES_DB,
) -> list[dict[str, Any]]:
    """Combine store run list with research notes, tags, and pinned baseline from notes.duckdb."""
    runs = list_store_runs(store_dir=store_dir)
    notes_map: dict[str, dict[str, Any]] = {}
    np = Path(notes_db_path)
    if np.exists():
        con = connect_duckdb_readonly(np)
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            if "run_notes" in tables:
                for rid, tags, note, pinned, updated in con.execute(
                    "SELECT run_id, tags, note, is_pinned_baseline, updated_at_utc FROM run_notes"
                ).fetchall():
                    notes_map[str(rid)] = {
                        "tags": str(tags or ""),
                        "note": str(note or ""),
                        "is_pinned_baseline": bool(pinned),
                        "updated_at_utc": str(updated or ""),
                    }
        finally:
            con.close()

    for r in runs:
        extra = notes_map.get(str(r["run_id"]), {})
        r["tags"] = extra.get("tags", "")
        r["note"] = extra.get("note", "")
        r["is_pinned_baseline"] = extra.get("is_pinned_baseline", False)
        r["updated_at_utc"] = extra.get("updated_at_utc", "")
    return runs


def get_run_report_text(run_id: str, store_dir: str | Path = DEFAULT_STORE_DIR) -> str:
    """Return report.md if present in run_dir, or synthesize a clean summary markdown report."""
    rdir = _resolve_run_dir(run_id, store_dir)
    rp = rdir / "report.md"
    if rp.exists():
        return rp.read_text(encoding="utf-8")
    s = get_run_summary(run_id, store_dir=store_dir)
    lines = [
        f"# Research Run Report: {s['run_id']}",
        f"- Strategy: `{s['strategy_id']}@{s['strategy_version']}`",
        f"- Mode: `{s['execution_mode']}` | Cost Profile: `{s['cost_profile']}` | Ambiguity: `{s['ambiguity_policy']}`",
        f"- Validation Status: **{s['validation_status']}**",
        f"- Net PnL: `{s['net_pnl']:.4f}` | Gross PnL: `{s['gross_pnl']:.4f}` | Fees: `{s['total_fees']:.4f}`",
        f"- PF (Trade / Leg): `{s['net_pf_trade']}` / `{s['net_pf_leg']}`",
        f"- Win Rate (Trade / Leg): `{s['trade_win_rate_pct']:.2f}%` / `{s['leg_win_rate_pct']:.2f}%`",
    ]
    return "\n".join(lines) + "\n"
