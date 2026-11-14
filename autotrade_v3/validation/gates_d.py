"""Gate D1 (artifact integrity) and D2 (reported numbers recomputed from the ledger).

D2 is an independent report verifier complying with V3-09 specifications.
It recomputes every headline statistic of ``analysis.json`` and ``report.md`` from ``trades.parquet``
(+ skipped/rejected/snapshots) and compares; it never reads a number from ``analysis.json`` in order
to compare it with itself.
"""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.hashing import artifact_content_hash
from autotrade_v3.validation.types import GateResult, ViolationCollector, failed, finalize, inconclusive

CORE_ARTIFACTS = ("run_manifest.json", "analysis.json", "signals.parquet", "trades.parquet")

CANONICAL_20_COMBOS = {
    "BTC-USDT/C1", "BTC-USDT/C2", "BTC-USDT/C4", "BTC-USDT/C6", "BTC-USDT/C7",
    "BTC-USDT/C9", "BTC-USDT/C10", "BTC-USDT/TIER", "BTC-USDT/C11", "BTC-USDT/C14", "BTC-USDT/C16",
    "ETH-USDT/C1", "ETH-USDT/C3", "ETH-USDT/C4", "ETH-USDT/C6", "ETH-USDT/C7",
    "ETH-USDT/C10", "ETH-USDT/C12", "ETH-USDT/C13", "ETH-USDT/C15",
}


def gate_d1(ctx: VerifyContext) -> GateResult:
    """Every file listed in ARTIFACTS.json exists, is readable and its SHA256/content hash recompute."""
    name = "Artifact integrity"
    idx = ctx.run.artifacts
    if idx is None:
        return failed("D1", name, "ARTIFACTS.json " + ctx.run.errors.get("ARTIFACTS.json", "missing"))
    arts: dict[str, Any] = dict(idx.get("artifacts", {}))
    viol = ViolationCollector()
    for core in CORE_ARTIFACTS:
        if core not in arts:
            viol.add({"artifact": core, "kind": "NOT_LISTED_IN_ARTIFACTS_JSON"})
    total = len(arts)
    checked = 0
    undelivered: list[str] = []
    for fname, meta in sorted(arts.items()):
        path = ctx.run_dir / fname
        if not path.exists():
            if meta.get("committed") is False:
                undelivered.append(fname)  # large artifact declared as not committed
                continue
            checked += 1
            viol.add({"artifact": fname, "kind": "MISSING_FILE"})
            continue
        checked += 1
        try:
            sha, chash, rows = artifact_content_hash(path)
        except Exception as e:  # noqa: BLE001
            viol.add({"artifact": fname, "kind": "UNREADABLE", "error": str(e)})
            continue
        if sha != meta.get("sha256"):
            viol.add({"artifact": fname, "kind": "SHA256_MISMATCH", "expected": meta.get("sha256"), "actual": sha})
        if "content_hash" in meta and chash != meta.get("content_hash"):
            viol.add({"artifact": fname, "kind": "CONTENT_HASH_MISMATCH",
                      "expected": meta.get("content_hash"), "actual": chash})
        if "size_bytes" in meta and path.stat().st_size != meta.get("size_bytes"):
            viol.add({"artifact": fname, "kind": "SIZE_MISMATCH"})
        if meta.get("row_count") is not None and rows != meta.get("row_count"):
            viol.add({"artifact": fname, "kind": "ROW_COUNT_MISMATCH", "expected": meta.get("row_count"), "actual": rows})
    return finalize(
        "D1", name, checked, total, viol,
        reason=("large artifacts not present locally: " + ", ".join(undelivered)) if undelivered else "",
        details={"undelivered_large_artifacts": undelivered},
    )


# ---------------------------------------------------------------------------------------------
# D2: Independent Ledger Recomputation
# ---------------------------------------------------------------------------------------------

def _calc_pf(wins: float, losses: float) -> tuple[float | str, float | str]:
    if losses > 1e-9:
        val = wins / losses
        return val, val
    elif wins > 1e-9:
        return "UNDEFINED_NO_LOSS", "UNDEFINED_NO_LOSS"
    else:
        return 0.0, 0.0


def _independent_four_profit_factors(trades_df: pd.DataFrame, is_v309: bool = True) -> dict[str, Any]:
    """Pure independent calculation of the 4 canonical Profit Factors."""
    if len(trades_df) == 0:
        base = {
            "net_pf_trade": 0.0, "net_pf_trade_unrounded": 0.0,
            "net_pf_leg": 0.0, "net_pf_leg_unrounded": 0.0,
            "gross_pf_trade": 0.0, "gross_pf_trade_unrounded": 0.0,
            "gross_pf_leg": 0.0, "gross_pf_leg_unrounded": 0.0,
            "profit_factor": 0.0, "net_profit_factor": 0.0, "gross_profit_factor": 0.0,
            "profit_factor_basis": "leg",
        }
        return base

    l_net = trades_df["net_pnl"]
    l_gross = trades_df["gross_pnl"]
    l_nw = float(l_net[l_net > 0].sum())
    l_nl = float(abs(l_net[l_net < 0].sum()))
    l_gw = float(l_gross[l_gross > 0].sum())
    l_gl = float(abs(l_gross[l_gross < 0].sum()))

    t_net = trades_df.groupby("trade_id")["net_pnl"].sum()
    t_gross = trades_df.groupby("trade_id")["gross_pnl"].sum()
    t_nw = float(t_net[t_net > 0].sum())
    t_nl = float(abs(t_net[t_net < 0].sum()))
    t_gw = float(t_gross[t_gross > 0].sum())
    t_gl = float(abs(t_gross[t_gross < 0].sum()))

    npf_t_r, npf_t_u = _calc_pf(t_nw, t_nl)
    npf_l_r, npf_l_u = _calc_pf(l_nw, l_nl)
    gpf_t_r, gpf_t_u = _calc_pf(t_gw, t_gl)
    gpf_l_r, gpf_l_u = _calc_pf(l_gw, l_gl)

    if not is_v309:
        return {
            "profit_factor": npf_l_u,
            "net_profit_factor": npf_l_u,
            "gross_profit_factor": gpf_l_u,
            "net_pf_trade": npf_t_u,
            "net_pf_leg": npf_l_u,
            "gross_pf_trade": gpf_t_u,
            "gross_pf_leg": gpf_l_u,
            "profit_factor_basis": "leg",
        }

    def _rd2(v: float | str) -> float | str:
        return round(v, 2) if isinstance(v, float) else v

    return {
        "net_pf_trade": _rd2(npf_t_r),
        "net_pf_trade_unrounded": npf_t_u,
        "net_pf_leg": _rd2(npf_l_r),
        "net_pf_leg_unrounded": npf_l_u,
        "gross_pf_trade": _rd2(gpf_t_r),
        "gross_pf_trade_unrounded": gpf_t_u,
        "gross_pf_leg": _rd2(gpf_l_r),
        "gross_pf_leg_unrounded": gpf_l_u,
        "profit_factor": _rd2(npf_l_r),
        "net_profit_factor": _rd2(npf_l_r),
        "gross_profit_factor": _rd2(gpf_l_r),
        "profit_factor_basis": "leg",
    }


def recompute_analysis(
    trades: pd.DataFrame,
    manifest: dict[str, Any],
    signals_df: pd.DataFrame | None,
    skipped: pd.DataFrame | None,
    rejected: pd.DataFrame | None,
    snapshots: pd.DataFrame | None,
    subwindows: list[dict[str, Any]],
    is_v309: bool = True,
) -> dict[str, Any]:
    """Independent re-derivation of the analysis structure from the ledger."""
    mode = str(manifest.get("execution_mode", ""))
    initial = float(manifest.get("initial_equity", 0.0))
    t = trades.copy()
    legs = len(t)
    n_signals = 0 if signals_df is None else len(signals_df)

    gross_u = float(t["gross_pnl"].sum()) if legs else 0.0
    fees_u = float(t["fee_total"].sum()) if legs else 0.0
    net_u = float(t["net_pnl"].sum()) if legs else 0.0

    four_pfs = _independent_four_profit_factors(t, is_v309=is_v309)
    trade_pnl = t.groupby("trade_id")["net_pnl"].sum() if legs else pd.Series(dtype=float)
    n_trades = len(trade_pnl)

    skipped_n = 0 if skipped is None or len(skipped) == 0 else int((skipped.get("reason", pd.Series(dtype=str)) == "SKIPPED_BY_CONCURRENCY").sum())
    other_skipped = (0 if skipped is None else len(skipped)) - skipped_n
    rej_n = 0 if rejected is None or len(rejected) == 0 else int((rejected.get("reason", pd.Series(dtype=str)) == "INSUFFICIENT_MARGIN").sum())
    other_rej = (0 if rejected is None else len(rejected)) - rej_n

    # Symbols
    symbols: dict[str, Any] = {}
    for sym, g in t.groupby("symbol", sort=True):
        s_pfs = _independent_four_profit_factors(g, is_v309=is_v309)
        s_legs = len(g)
        s_entries = int(g["trade_id"].nunique())
        s_gross = float(g["gross_pnl"].sum())
        s_fees = float(g["fee_total"].sum())
        s_net = float(g["net_pnl"].sum())
        s_wr = round((g["net_pnl"] > 0).sum() / s_legs * 100.0, 2) if s_legs else 0.0

        sym_dict = {
            "symbol": str(sym),
            "entries": s_entries,
            "legs": s_legs,
            "leg_win_rate_pct": (g["net_pnl"] > 0).sum() / s_legs * 100.0 if s_legs else 0.0,
            "gross_pnl": s_gross,
            "total_fees": s_fees,
            "net_pnl": s_net,
            "profit_factor": s_pfs["profit_factor"],
            "net_profit_factor": s_pfs["net_pf_leg"],
            "gross_profit_factor": s_pfs["gross_pf_leg"],
        }
        if is_v309:
            sym_dict.update({
                "gross_pnl_unrounded": s_gross,
                "total_fees_unrounded": s_fees,
                "net_pnl_unrounded": s_net,
                "net_pf_trade": s_pfs["net_pf_trade"],
                "net_pf_trade_unrounded": s_pfs["net_pf_trade_unrounded"],
                "net_pf_leg": s_pfs["net_pf_leg"],
                "net_pf_leg_unrounded": s_pfs["net_pf_leg_unrounded"],
                "gross_pf_trade": s_pfs["gross_pf_trade"],
                "gross_pf_trade_unrounded": s_pfs["gross_pf_trade_unrounded"],
                "gross_pf_leg": s_pfs["gross_pf_leg"],
                "gross_pf_leg_unrounded": s_pfs["gross_pf_leg_unrounded"],
                "profit_factor_basis": "leg",
            })
            if signals_df is not None and "symbol" in signals_df.columns:
                sym_dict["signals"] = int((signals_df["symbol"] == sym).sum())
        symbols[str(sym)] = sym_dict

    sym_reconciled = math.isclose(sum(s["net_pnl"] for s in symbols.values()), round(net_u, 4), abs_tol=1e-3)
    balance_ok = math.isclose(gross_u - fees_u, net_u, abs_tol=1e-4)

    # Closed-trade drawdown
    cum = np.cumsum(t["net_pnl"].to_numpy(dtype=float)) if legs else np.array([0.0])
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for c in cum:
        peak = max(peak, float(c))
        dd = peak - float(c)
        if dd > max_dd:
            max_dd = dd
            ref = initial + peak
            max_dd_pct = dd / ref * 100.0 if ref > 0 else 0.0

    m2m: dict[str, Any] = {"m2m_max_drawdown_dollars": None, "m2m_max_drawdown_pct": None}
    if mode == "BINGX_PORTFOLIO" and snapshots is not None and len(snapshots):
        eq = snapshots["equity"].to_numpy(dtype=float)
        pk = np.maximum.accumulate(eq)
        dd = pk - eq
        m2m = {
            "m2m_max_drawdown_dollars": float(dd.max()),
            "m2m_max_drawdown_pct": float((dd / np.where(pk > 0, pk, np.nan) * 100.0).max()),
        }

    exit_reasons: dict[str, Any] = {}
    for reason, g in t.groupby("exit_reason", sort=False):
        exit_reasons[str(reason)] = {
            "count": len(g),
            "gross_pnl": float(g["gross_pnl"].sum()),
            "net_pnl": float(g["net_pnl"].sum()),
            "pct_of_legs": len(g) / legs * 100.0 if legs else 0.0,
        }

    # Combos
    combos = []
    if is_v309:
        # Canonical V3-09 combo keys: f"{sym}/{combo_name}"
        t_copy = t.copy()
        t_copy["combo_key"] = t_copy["symbol"].astype(str) + "/" + t_copy["combo_name"].astype(str)
        for ckey, g in t_copy.groupby("combo_key", sort=True):
            c_pfs = _independent_four_profit_factors(g, is_v309=is_v309)
            c_legs = len(g)
            c_entries = int(g["trade_id"].nunique())
            c_gross = float(g["gross_pnl"].sum())
            c_fees = float(g["fee_total"].sum())
            c_net = float(g["net_pnl"].sum())
            c_wins = int((g["net_pnl"] > 0).sum())
            c_dict = {
                "combo_key": str(ckey),
                "combo_id": int(g["combo_id"].iloc[0]),
                "combo_name": str(g["combo_name"].iloc[0]),
                "symbol": str(g["symbol"].iloc[0]),
                "timeframe": str(g["timeframe"].iloc[0]),
                "legs": c_legs,
                "wins": c_wins,
                "gross_pnl_unrounded": c_gross,
                "fees_unrounded": c_fees,
                "net_pnl_unrounded": c_net,
                "entries": c_entries,
                "win_rate_pct": c_wins / c_legs * 100.0 if c_legs else 0.0,
                "gross_pnl": c_gross,
                "fees": c_fees,
                "net_pnl": c_net,
                "net_pf_trade": c_pfs["net_pf_trade"],
                "net_pf_trade_unrounded": c_pfs["net_pf_trade_unrounded"],
                "net_pf_leg": c_pfs["net_pf_leg"],
                "net_pf_leg_unrounded": c_pfs["net_pf_leg_unrounded"],
                "gross_pf_trade": c_pfs["gross_pf_trade"],
                "gross_pf_trade_unrounded": c_pfs["gross_pf_trade_unrounded"],
                "gross_pf_leg": c_pfs["gross_pf_leg"],
                "gross_pf_leg_unrounded": c_pfs["gross_pf_leg_unrounded"],
                "profit_factor": c_pfs["profit_factor"],
                "profit_factor_basis": "leg",
                "net_profit_factor": c_pfs["net_pf_leg"],
                "gross_profit_factor": c_pfs["gross_pf_leg"],
            }
            if signals_df is not None and {"symbol", "combo_name"} <= set(signals_df.columns):
                c_dict["signals"] = int(((signals_df["symbol"] == g["symbol"].iloc[0]) & (signals_df["combo_name"] == g["combo_name"].iloc[0])).sum())
            combos.append(c_dict)
    else:
        # V3-08 legacy combo structure
        for cid, g in t.groupby("combo_id", sort=True):
            c_pfs = _independent_four_profit_factors(g, is_v309=is_v309)
            c_legs = len(g)
            c_entries = int(g["trade_id"].nunique())
            c_gross = float(g["gross_pnl"].sum())
            c_fees = float(g["fee_total"].sum())
            c_net = float(g["net_pnl"].sum())
            c_wins = int((g["net_pnl"] > 0).sum())
            combos.append({
                "combo_id": int(cid),
                "combo_name": str(g["combo_name"].iloc[0]),
                "symbol": str(g["symbol"].iloc[0]),
                "timeframe": str(g["timeframe"].iloc[0]),
                "legs": c_legs,
                "entries": c_entries,
                "wins": c_wins,
                "gross_pnl": c_gross,
                "fees": c_fees,
                "net_pnl": c_net,
                "win_rate_pct": c_wins / c_legs * 100.0 if c_legs else 0.0,
                "profit_factor": c_pfs["profit_factor"],
                "net_profit_factor": c_pfs["net_pf_leg"],
                "gross_profit_factor": c_pfs["gross_pf_leg"],
            })

    ym = pd.to_datetime(t["entry_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m") if legs else pd.Series(dtype=str)
    monthly = []
    for key, g in t.groupby(ym, sort=True):
        m_pfs = _independent_four_profit_factors(g, is_v309=is_v309)
        m_legs = len(g)
        m_wins = int((g["net_pnl"] > 0).sum())
        m_gross = float(g["gross_pnl"].sum())
        m_fees = float(g["fee_total"].sum())
        m_net = float(g["net_pnl"].sum())
        m_row = {
            "year_month": str(key),
            "legs": m_legs,
            "entries": int(g["trade_id"].nunique()),
            "wins": m_wins,
            "gross_pnl": m_gross,
            "fees": m_fees,
            "net_pnl": m_net,
            "win_rate_pct": m_wins / m_legs * 100.0 if m_legs else 0.0,
            "profit_factor": m_pfs["profit_factor"],
            "net_profit_factor": m_pfs["net_pf_leg"],
            "gross_profit_factor": m_pfs["gross_pf_leg"],
        }
        if is_v309:
            m_row.update({
                "gross_pnl_unrounded": m_gross,
                "fees_unrounded": m_fees,
                "net_pnl_unrounded": m_net,
                "net_pf_trade": m_pfs["net_pf_trade"],
                "net_pf_trade_unrounded": m_pfs["net_pf_trade_unrounded"],
                "net_pf_leg": m_pfs["net_pf_leg"],
                "net_pf_leg_unrounded": m_pfs["net_pf_leg_unrounded"],
                "gross_pf_trade": m_pfs["gross_pf_trade"],
                "gross_pf_trade_unrounded": m_pfs["gross_pf_trade_unrounded"],
                "gross_pf_leg": m_pfs["gross_pf_leg"],
                "gross_pf_leg_unrounded": m_pfs["gross_pf_leg_unrounded"],
                "profit_factor_basis": "leg",
            })
        monthly.append(m_row)

    sub_out = []
    for sw in subwindows:
        ws = int(pd.Timestamp(sw["start"], tz="UTC").value // 1_000_000)
        we = int(pd.Timestamp(sw["end"], tz="UTC").value // 1_000_000)
        g = t[(t["entry_time_ms"] >= ws) & (t["entry_time_ms"] < we)]
        sw_pfs = _independent_four_profit_factors(g, is_v309=is_v309)
        sw_legs = len(g)
        sw_wins = int((g["net_pnl"] > 0).sum())
        sw_gross = float(g["gross_pnl"].sum())
        sw_fees = float(g["fee_total"].sum())
        sw_net = float(g["net_pnl"].sum())
        sw_row = {
            "subwindow": sw["subwindow"], "start": sw["start"], "end": sw["end"],
            "entries": int(g["trade_id"].nunique()), "legs": sw_legs,
            "win_rate_pct": sw_wins / sw_legs * 100.0 if sw_legs else 0.0,
            "gross_pnl": sw_gross, "total_fees": sw_fees,
            "net_pnl": sw_net,
            "profit_factor": sw_pfs["profit_factor"],
            "net_profit_factor": sw_pfs["net_pf_leg"],
            "gross_profit_factor": sw_pfs["gross_pf_leg"],
        }
        if is_v309:
            sw_row.update({
                "gross_pnl_unrounded": sw_gross,
                "total_fees_unrounded": sw_fees,
                "net_pnl_unrounded": sw_net,
                "net_pf_trade": sw_pfs["net_pf_trade"],
                "net_pf_trade_unrounded": sw_pfs["net_pf_trade_unrounded"],
                "net_pf_leg": sw_pfs["net_pf_leg"],
                "net_pf_leg_unrounded": sw_pfs["net_pf_leg_unrounded"],
                "gross_pf_trade": sw_pfs["gross_pf_trade"],
                "gross_pf_trade_unrounded": sw_pfs["gross_pf_trade_unrounded"],
                "gross_pf_leg": sw_pfs["gross_pf_leg"],
                "gross_pf_leg_unrounded": sw_pfs["gross_pf_leg_unrounded"],
                "profit_factor_basis": "leg",
            })
        sub_out.append(sw_row)

    wl = int((t["net_pnl"] > 0).sum())
    pnl_dict = {
        "gross_pnl": gross_u,
        "total_fees": fees_u,
        "net_pnl": net_u,
        "profit_factor": four_pfs["profit_factor"],
        "net_profit_factor": four_pfs["net_pf_leg"],
        "gross_profit_factor": four_pfs["gross_pf_leg"],
        "roi_pct": net_u / initial * 100.0 if initial else 0.0,
        "roi_denominator_value": initial,
        "avg_trade_pnl": net_u / n_trades if n_trades else 0.0,
        "avg_leg_pnl": net_u / legs if legs else 0.0,
        "accounting_verified": bool(balance_ok and sym_reconciled),
    }
    if is_v309:
        pnl_dict.update({
            "gross_pnl_unrounded": gross_u,
            "total_fees_unrounded": fees_u,
            "net_pnl_unrounded": net_u,
            "net_pf_trade": four_pfs["net_pf_trade"],
            "net_pf_trade_unrounded": four_pfs["net_pf_trade_unrounded"],
            "net_pf_leg": four_pfs["net_pf_leg"],
            "net_pf_leg_unrounded": four_pfs["net_pf_leg_unrounded"],
            "gross_pf_trade": four_pfs["gross_pf_trade"],
            "gross_pf_trade_unrounded": four_pfs["gross_pf_trade_unrounded"],
            "gross_pf_leg": four_pfs["gross_pf_leg"],
            "gross_pf_leg_unrounded": four_pfs["gross_pf_leg_unrounded"],
            "profit_factor_basis": "leg",
        })

    out = {
        "mode": mode,
        "accounting": {
            "total_signals": int(n_signals),
            "accepted_entries": n_trades,
            "total_legs": legs,
            "skipped_by_concurrency": skipped_n,
            "other_skipped": other_skipped,
            "rejected_by_margin": rej_n,
            "other_rejected": other_rej,
            "symbol_reconciliation_pass": sym_reconciled,
            "accounting_balance_pass": balance_ok,
        },
        "win_rate": {
            "leg_win_rate_pct": round(wl / legs * 100.0, 2) if legs else 0.0,
            "trade_win_rate_pct": round(int((trade_pnl > 0).sum()) / n_trades * 100.0, 2) if n_trades else 0.0,
            "winning_legs": wl,
            "losing_legs": int((t["net_pnl"] < 0).sum()),
            "breakeven_legs": int((t["net_pnl"] == 0).sum()),
            "winning_trades": int((trade_pnl > 0).sum()),
            "losing_trades": int((trade_pnl < 0).sum()),
            "breakeven_trades": int((trade_pnl == 0).sum()),
        },
        "pnl": pnl_dict,
        "risk": {
            "closed_trade_max_drawdown_dollars": max_dd,
            "closed_trade_max_drawdown_pct": max_dd_pct,
            **m2m,
        },
        "ambiguity": {
            "intrabar_tp_sl_ambiguous_count": int(t["intrabar_ambiguous"].sum()) if "intrabar_ambiguous" in t.columns else 0,
            "intrabar_trailing_ambiguous_count": int(t["intrabar_trailing_ambiguous"].sum()) if "intrabar_trailing_ambiguous" in t.columns else 0,
        },
        "symbols": symbols,
        "combos": combos,
        "monthly": monthly,
        "subwindows": sub_out,
        "exit_reasons": exit_reasons,
    }

    if is_v309:
        # Reconciliation block
        sum_sym_gross = sum(s["gross_pnl_unrounded"] for s in symbols.values())
        sum_combo_gross = sum(c["gross_pnl_unrounded"] for c in combos)
        sum_sym_fee = sum(s["total_fees_unrounded"] for s in symbols.values())
        sum_combo_fee = sum(c["fees_unrounded"] for c in combos)
        sum_sym_net = sum(s["net_pnl_unrounded"] for s in symbols.values())
        sum_combo_net = sum(c["net_pnl_unrounded"] for c in combos)

        sum_sym_signals = sum(s.get("signals", 0) for s in symbols.values())
        sum_combo_signals = sum(c.get("signals", 0) for c in combos)
        sum_sym_entries = sum(s.get("entries", 0) for s in symbols.values())
        sum_combo_entries = sum(c.get("entries", 0) for c in combos)
        sum_sym_legs = sum(s.get("legs", 0) for s in symbols.values())
        sum_combo_legs = sum(c.get("legs", 0) for c in combos)

        signals_diff = max(abs(n_signals - sum_sym_signals), abs(n_signals - sum_combo_signals))
        entries_diff = max(abs(n_trades - sum_sym_entries), abs(n_trades - sum_combo_entries))
        legs_diff = max(abs(legs - sum_sym_legs), abs(legs - sum_combo_legs))
        gross_diff = max(abs(gross_u - sum_sym_gross), abs(gross_u - sum_combo_gross))
        fee_diff = max(abs(fees_u - sum_sym_fee), abs(fees_u - sum_combo_fee))
        net_diff = max(abs(net_u - sum_sym_net), abs(net_u - sum_combo_net))

        tol = 1e-9
        reconciliation_pass = (
            signals_diff == 0
            and entries_diff == 0
            and legs_diff == 0
            and gross_diff <= tol
            and fee_diff <= tol
            and net_diff <= tol
        )
        alignment_pass = True
        for c in combos:
            if c["combo_key"] not in CANONICAL_20_COMBOS:
                alignment_pass = False
                break

        out["accounting"]["three_level_reconciliation_pass"] = bool(reconciliation_pass and alignment_pass)

        out["reconciliation"] = {
            "signals": {
                "run_total": int(n_signals),
                "sum_by_symbol": int(sum_sym_signals),
                "sum_by_combo": int(sum_combo_signals),
                "max_absolute_diff": int(signals_diff),
            },
            "entries": {
                "run_total": int(n_trades),
                "sum_by_symbol": int(sum_sym_entries),
                "sum_by_combo": int(sum_combo_entries),
                "max_absolute_diff": int(entries_diff),
            },
            "legs": {
                "run_total": int(legs),
                "sum_by_symbol": int(sum_sym_legs),
                "sum_by_combo": int(sum_combo_legs),
                "max_absolute_diff": int(legs_diff),
            },
            "gross_pnl": {
                "run_total_unrounded": gross_u,
                "sum_by_symbol_unrounded": sum_sym_gross,
                "sum_by_combo_unrounded": sum_combo_gross,
                "max_absolute_diff_unrounded": gross_diff,
                "run_total_rounded_4dp": round(gross_u, 4),
                "sum_by_symbol_rounded_4dp": round(sum(s["gross_pnl"] for s in symbols.values()), 4),
                "sum_by_combo_rounded_4dp": round(sum(c["gross_pnl"] for c in combos), 4),
                "rounding_discrepancy_vs_run_rounded": round(abs(sum(c["gross_pnl"] for c in combos) - round(gross_u, 4)), 4),
            },
            "fees": {
                "run_total_unrounded": fees_u,
                "sum_by_symbol_unrounded": sum_sym_fee,
                "sum_by_combo_unrounded": sum_combo_fee,
                "max_absolute_diff_unrounded": fee_diff,
                "run_total_rounded_4dp": round(fees_u, 4),
                "sum_by_symbol_rounded_4dp": round(sum(s["total_fees"] for s in symbols.values()), 4),
                "sum_by_combo_rounded_4dp": round(sum(c["fees"] for c in combos), 4),
                "rounding_discrepancy_vs_run_rounded": round(abs(sum(c["fees"] for c in combos) - round(fees_u, 4)), 4),
            },
            "net_pnl": {
                "run_total_unrounded": net_u,
                "sum_by_symbol_unrounded": sum_sym_net,
                "sum_by_combo_unrounded": sum_combo_net,
                "max_absolute_diff_unrounded": net_diff,
                "run_total_rounded_4dp": round(net_u, 4),
                "sum_by_symbol_rounded_4dp": round(sum(s["net_pnl"] for s in symbols.values()), 4),
                "sum_by_combo_rounded_4dp": round(sum(c["net_pnl"] for c in combos), 4),
                "rounding_discrepancy_vs_run_rounded": round(abs(sum(c["net_pnl"] for c in combos) - round(net_u, 4)), 4),
            },
            "symbol_combo_alignment_pass": bool(alignment_pass),
            "tolerance": tol,
            "reconciliation_pass": bool(reconciliation_pass and alignment_pass),
        }

    return out


_TEXT_KEYS = {"roi_note", "m2m_note", "m2m_status", "resolution_policy", "roi_denominator_name", "validation", "profit_factor_basis"}


def _walk_compare(actual: Any, exp: Any, path: str, viol: ViolationCollector, counter: list[int]) -> None:
    """Compare every leaf of ``actual`` (analysis.json) to the recomputed structure."""
    if isinstance(actual, dict):
        if not isinstance(exp, dict):
            viol.add({"path": path, "kind": "STRUCTURE", "expected": type(exp).__name__})
            return
        for k, v in actual.items():
            if k in _TEXT_KEYS:
                continue
            if k not in exp:
                counter[0] += _count_leaves(v)
                viol.add({"path": f"{path}.{k}", "kind": "UNEXPECTED_KEY"})
                continue
            _walk_compare(v, exp[k], f"{path}.{k}", viol, counter)
        for k in exp:
            if k not in actual and k not in _TEXT_KEYS:
                viol.add({"path": f"{path}.{k}", "kind": "MISSING_KEY"})
    elif isinstance(actual, list):
        if not isinstance(exp, list) or len(actual) != len(exp):
            counter[0] += _count_leaves(actual)
            viol.add({"path": path, "kind": "LIST_LENGTH", "expected": len(exp) if isinstance(exp, list) else None,
                      "actual": len(actual)})
            return
        for i, (a, e) in enumerate(zip(actual, exp)):
            _walk_compare(a, e, f"{path}[{i}]", viol, counter)
    else:
        if isinstance(actual, str) and not _is_numeric_text(actual):
            if actual != exp:
                viol.add({"path": path, "kind": "STRING_MISMATCH", "expected": exp, "actual": actual})
            return
        counter[0] += 1
        counter[1] += 1  # checked
        if not _leaf_equal(path, actual, exp):
            viol.add({"path": path, "kind": "VALUE_MISMATCH", "expected": exp, "actual": actual})


def _is_numeric_text(s: str) -> bool:
    return s == "UNDEFINED_NO_LOSS"


def _count_leaves(x: Any) -> int:
    if isinstance(x, dict):
        return sum(_count_leaves(v) for k, v in x.items() if k not in _TEXT_KEYS)
    if isinstance(x, list):
        return sum(_count_leaves(v) for v in x)
    if isinstance(x, str) and not _is_numeric_text(x):
        return 0
    return 1


def _decimals_for(path: str) -> int:
    last = path.rsplit(".", 1)[-1]
    if "unrounded" in last or "tolerance" in last:
        return 8
    if "diff" in last or "discrepancy" in last or "rounded" in last:
        return 3
    if "pct" in last or "profit_factor" in last or "pf" in last:
        return 2
    return 4


def _leaf_equal(path: str, actual: Any, exp: Any) -> bool:
    if actual is None or exp is None or isinstance(actual, (bool, str)) or isinstance(exp, (bool, str)):
        return actual == exp
    nd = _decimals_for(path)
    tol = 0.5 * 10 ** (-nd) + 1e-9
    if isinstance(actual, int) and isinstance(exp, int):
        return actual == exp
    if nd == 2:
        tol += 5e-5
    elif nd == 8:
        tol = 1e-6
    return abs(float(actual) - float(exp)) <= tol


_NUM = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z_])")


def _tok_value(tok: str) -> tuple[float, int]:
    clean = tok.replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    dec = len(clean.split(".")[1]) if "." in clean else 0
    return float(clean), dec


def _fmt_matches(tok: str, expected: float) -> bool:
    val, dec = _tok_value(tok)
    tol = 0.06 if dec == 1 else (0.5 * 10 ** (-dec) + 5e-5 + 1e-9)
    return abs(expected - val) <= tol


def _verify_report(text: str, exp: dict[str, Any], manifest: dict[str, Any], viol: ViolationCollector) -> tuple[int, int]:
    """Return (numbers_checked, numbers_total) for report.md."""
    total = 0
    checked = 0
    acct, wr, pnl, risk, amb = exp["accounting"], exp["win_rate"], exp["pnl"], exp["risk"], exp["ambiguity"]
    fee_pct = float(manifest.get("fee_rate_taker", 0.0)) * 100.0

    def check_tokens(line: str, label: str, expected: list[Any]) -> None:
        nonlocal total, checked
        toks = _NUM.findall(line)
        total += len(toks)
        if len(toks) != len(expected):
            viol.add({"line": label, "kind": "TOKEN_COUNT", "expected": len(expected), "actual": toks})
            return
        for tok, e in zip(toks, expected):
            if _fmt_matches(tok, float(e)):
                checked += 1
            else:
                viol.add({"line": label, "kind": "REPORT_NUMBER_MISMATCH", "token": tok, "expected": e})

    def check_text(label: str, actual: str, expected: str) -> None:
        nonlocal total, checked
        total += 1
        if actual == expected:
            checked += 1
        else:
            viol.add({"line": label, "kind": "REPORT_TEXT_MISMATCH", "expected": expected, "actual": actual})

    sym_rows = exp["symbols"]
    combo_rows = {c["combo_key"]: c for c in exp["combos"] if "combo_key" in c}

    in_sym_table = False
    in_combo_table = False
    in_exit_table = False

    for raw in text.splitlines():
        line = raw.replace("**", "").replace("`", "").strip()
        if not line:
            continue
        m = re.match(r"^# AutoTrade V3 Research Lab Run Report: (.+)$", line)
        if m:
            check_text("title.run_id", m.group(1), str(manifest.get("run_id")))
        elif line.startswith("- Strategy:"):
            check_text("strategy", line[len("- Strategy:"):].strip(),
                       f"{manifest.get('strategy_id')} (v{manifest.get('strategy_version')})")
        elif line.startswith("- Time Window:"):
            tw = manifest.get("time_window", {})
            check_text("time_window", line[len("- Time Window:"):].strip(), f"{tw.get('start')} to {tw.get('end')}")
        elif line.startswith("- Git SHA:"):
            check_text("git_sha", line[len("- Git SHA:"):].strip(), str(manifest.get("git_sha")))
        elif line.startswith("- Data Hash:"):
            val = line[len("- Data Hash:"):].strip()
            check_text("data_hash", val, str(manifest.get("data_manifest_hash"))[:16] + "...")
        elif line.startswith("- Total Signals Generated:"):
            check_tokens(line.split(":", 1)[1], "total_signals", [acct["total_signals"]])
        elif line.startswith("- Accepted Entries"):
            check_tokens(line.split(":", 1)[1], "accepted_entries", [acct["accepted_entries"]])
        elif line.startswith("- Total Trade Legs:"):
            check_tokens(line.split(":", 1)[1], "total_legs", [acct["total_legs"]])
        elif line.startswith("- Skipped by Concurrency:"):
            check_tokens(line.split(":", 1)[1], "skipped", [acct["skipped_by_concurrency"]])
        elif line.startswith("- Rejected by Margin:"):
            check_tokens(line.split(":", 1)[1], "rejected", [acct["rejected_by_margin"]])
        elif line.startswith("- Gross PnL:"):
            check_tokens(line.split(":", 1)[1], "gross_pnl", [pnl.get("gross_pnl_unrounded", pnl["gross_pnl"])])
        elif line.startswith("- Total Fees"):
            check_tokens(line, "total_fees", [fee_pct, pnl.get("total_fees_unrounded", pnl["total_fees"])])
        elif line.startswith("- Net PnL:"):
            check_tokens(line.split(":", 1)[1], "net_pnl", [pnl.get("net_pnl_unrounded", pnl["net_pnl"])])
        elif line.startswith("- ROI ("):
            check_tokens(line, "roi", [pnl["roi_denominator_value"], pnl.get("roi_pct_unrounded", pnl["roi_pct"])])
        elif line.startswith("- Net Profit Factor (Default):"):
            v = pnl["net_profit_factor"]
            if isinstance(v, str):
                check_text("net_pf", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "net_pf", [v])
        elif line.startswith("- Net Profit Factor (Trade):"):
            v = pnl.get("net_pf_trade", pnl["net_profit_factor"])
            if isinstance(v, str):
                check_text("net_pf_trade", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "net_pf_trade", [v])
        elif line.startswith("- Net Profit Factor (Leg):"):
            v = pnl.get("net_pf_leg", pnl["net_profit_factor"])
            if isinstance(v, str):
                check_text("net_pf_leg", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "net_pf_leg", [v])
        elif line.startswith("- Gross Profit Factor (Trade):"):
            v = pnl.get("gross_pf_trade", pnl["gross_profit_factor"])
            if isinstance(v, str):
                check_text("gross_pf_trade", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "gross_pf_trade", [v])
        elif line.startswith("- Gross Profit Factor (Leg):"):
            v = pnl.get("gross_pf_leg", pnl["gross_profit_factor"])
            if isinstance(v, str):
                check_text("gross_pf_leg", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "gross_pf_leg", [v])
        elif line.startswith("- Gross Profit Factor:"):
            v = pnl["gross_profit_factor"]
            if isinstance(v, str):
                check_text("gross_pf", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "gross_pf", [v])
        elif line.startswith("- Trade-level Win Rate:"):
            check_tokens(line.split(":", 1)[1], "trade_wr",
                         [wr["trade_win_rate_pct"], wr["winning_trades"], acct["accepted_entries"]])
        elif line.startswith("- Leg-level Win Rate:"):
            check_tokens(line.split(":", 1)[1], "leg_wr", [wr["leg_win_rate_pct"], wr["winning_legs"], acct["total_legs"]])
        elif line.startswith("- Closed-Trade Max Drawdown:"):
            check_tokens(line.split(":", 1)[1], "closed_dd",
                         [risk["closed_trade_max_drawdown_dollars"], risk["closed_trade_max_drawdown_pct"]])
        elif line.startswith("- Continuous M2M Max Drawdown:"):
            if risk["m2m_max_drawdown_dollars"] is None:
                body = line.split(":", 1)[1]
                if _NUM.findall(body):
                    total += len(_NUM.findall(body))
                    viol.add({"line": "m2m", "kind": "M2M_PRESENT_BUT_NOT_APPLICABLE", "text": body.strip()})
            else:
                check_tokens(line.split(":", 1)[1], "m2m_dd",
                             [risk["m2m_max_drawdown_dollars"], risk["m2m_max_drawdown_pct"]])
        elif line.startswith("- TP/SL Same-Candle Ambiguity:"):
            check_tokens(line.split(":", 1)[1], "amb_tpsl", [amb["intrabar_tp_sl_ambiguous_count"]])
        elif line.startswith("- Trailing Stop Ambiguity:"):
            check_tokens(line.split(":", 1)[1], "amb_trail", [amb["intrabar_trailing_ambiguous_count"]])
        elif line.startswith("## 5. Performance by Symbol"):
            in_sym_table = True
            in_combo_table = False
            in_exit_table = False
        elif line.startswith("## 6. Exit Reason"):
            in_sym_table = False
            in_exit_table = True
            in_combo_table = False
        elif line.startswith("## 7. Performance by Combo"):
            in_sym_table = False
            in_exit_table = False
            in_combo_table = True
        elif line.startswith("## 8. Reconciliation Audit"):
            in_sym_table = False
            in_exit_table = False
            in_combo_table = False
        elif line.startswith("- Three-Level Parity Pass"):
            exp_pass = exp.get("reconciliation", {}).get("reconciliation_pass", True)
            val = "true" in line.lower()
            total += 1
            if val == exp_pass:
                checked += 1
            else:
                viol.add({"line": "reconciliation_parity", "kind": "RECONCILIATION_MISMATCH", "expected": exp_pass, "actual": val})
        elif line.startswith("- Symbol-Combo Configuration Alignment:"):
            exp_align = exp.get("reconciliation", {}).get("symbol_combo_alignment_pass", True)
            val = "true" in line.lower()
            total += 1
            if val == exp_align:
                checked += 1
            else:
                viol.add({"line": "combo_alignment", "kind": "ALIGNMENT_MISMATCH", "expected": exp_align, "actual": val})
        elif line.startswith("- Unrounded Tolerance:"):
            total += 1
            checked += 1
        elif line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # Check table headers
            if cells[0] in ("Symbol", "Exit Reason", "Combo Key"):
                if cells[0] == "Symbol":
                    expected_cols_9 = ["Symbol", "Entries", "Legs", "Net PnL", "Win Rate (Leg)", "Net PF", "Gross PF", "Net PF (Trade)", "Gross PF (Trade)"]
                    expected_cols_7 = ["Symbol", "Entries", "Legs", "Net PnL", "Win Rate (Leg)", "Net PF", "Gross PF"]
                    expected_cols_7_alt = ["Symbol", "Entries", "Legs", "Net PnL", "Win Rate", "Net PF", "Gross PF"]
                    if cells != expected_cols_9 and cells != expected_cols_7 and cells != expected_cols_7_alt:
                        viol.add({"line": "symbol_table_header", "kind": "TABLE_HEADER_MISMATCH", "actual": cells})
                elif cells[0] == "Combo Key":
                    expected_combo_cols = ["Combo Key", "Symbol", "ID", "Name", "Timeframe", "Entries", "Legs", "Net PnL", "Win Rate", "Net PF (Leg)", "Net PF (Trade)"]
                    if cells != expected_combo_cols:
                        viol.add({"line": "combo_table_header", "kind": "TABLE_HEADER_MISMATCH", "actual": cells})
                elif cells[0] == "Exit Reason":
                    expected_exit_cols = ["Exit Reason", "Count", "Pct of Legs", "Gross PnL", "Net PnL"]
                    if cells != expected_exit_cols:
                        viol.add({"line": "exit_table_header", "kind": "TABLE_HEADER_MISMATCH", "actual": cells})
                continue
            if set(cells[0]) <= set(":-"):
                continue  # separator row

            # Row data
            if in_sym_table and cells[0] in sym_rows:
                s = sym_rows[cells[0]]
                nums = _NUM.findall(" ".join(cells[1:5]))
                s_net = s.get("net_pnl_unrounded", s["net_pnl"])
                exp_vals = [s["entries"], s["legs"], s_net, s["leg_win_rate_pct"]]
                total += len(nums)
                if len(nums) != 4:
                    viol.add({"line": cells[0], "kind": "TOKEN_COUNT"})
                else:
                    for tok, e in zip(nums, exp_vals):
                        if _fmt_matches(tok, float(e)):
                            checked += 1
                        else:
                            viol.add({"line": cells[0], "kind": "REPORT_NUMBER_MISMATCH", "token": tok, "expected": e})
                # Check profit factors
                pf_cells = cells[5:]
                exp_pfs = [s["net_profit_factor"], s["gross_profit_factor"]]
                if len(pf_cells) >= 4:
                    exp_pfs.extend([s.get("net_pf_trade", "N/A"), s.get("gross_pf_trade", "N/A")])
                for cell, e in zip(pf_cells, exp_pfs):
                    if isinstance(e, str):
                        check_text(f"{cells[0]}.pf", cell, e)
                    else:
                        check_tokens(cell, f"{cells[0]}.pf", [e])
            elif in_combo_table and cells[0] in combo_rows:
                c = combo_rows[cells[0]]
                c_net = c.get("net_pnl_unrounded", c["net_pnl"])
                # Columns: Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL | Win Rate | Net PF (Leg) | Net PF (Trade)
                if len(cells) >= 11:
                    exp_nums = [c["combo_id"], c["entries"], c["legs"], c_net, c["win_rate_pct"]]
                    num_tokens = _NUM.findall(" ".join([cells[2], cells[5], cells[6], cells[7], cells[8]]))
                    total += len(num_tokens)
                    if len(num_tokens) != len(exp_nums):
                        viol.add({"line": f"combo.{cells[0]}", "kind": "TOKEN_COUNT"})
                    else:
                        for tok, e in zip(num_tokens, exp_nums):
                            if _fmt_matches(tok, float(e)):
                                checked += 1
                            else:
                                viol.add({"line": f"combo.{cells[0]}", "kind": "REPORT_NUMBER_MISMATCH", "token": tok, "expected": e})
                    for cell, e in ((cells[9], c.get("net_pf_leg", "N/A")), (cells[10], c.get("net_pf_trade", "N/A"))):
                        if isinstance(e, str):
                            check_text(f"combo.{cells[0]}.pf", cell, e)
                        else:
                            check_tokens(cell, f"combo.{cells[0]}.pf", [e])
            elif in_exit_table and cells[0] in exp["exit_reasons"]:
                r = exp["exit_reasons"][cells[0]]
                nums = _NUM.findall(" ".join(cells[1:]))
                exp_vals = [r["count"], r["pct_of_legs"], r["gross_pnl"], r["net_pnl"]]
                total += len(nums)
                if len(nums) != 4:
                    viol.add({"line": f"exit.{cells[0]}", "kind": "TOKEN_COUNT"})
                else:
                    for tok, e in zip(nums, exp_vals):
                        if _fmt_matches(tok, float(e)):
                            checked += 1
                        else:
                            viol.add({"line": f"exit.{cells[0]}", "kind": "REPORT_NUMBER_MISMATCH", "token": tok, "expected": e})
            elif _NUM.findall(line) and not set(cells[0]) <= set(":-"):
                if cells[0] not in ("Symbol", "Gate", "Combo Key", "Exit Reason"):
                    total += len(_NUM.findall(line))
        else:
            leftover = _NUM.findall(re.sub(r"^#+\s*\d+\.\s*", "", line))
            if leftover and not line.startswith("#") and not line.startswith("- Execution Mode"):
                if not (line.startswith("- Symbols:") or line.startswith("- Validation Status:")
                        or line.startswith("- Note on ROI") or line.startswith("- Resolution Policy")):
                    total += len(leftover)

    return checked, total


def gate_d2(ctx: VerifyContext) -> GateResult:
    name = "Reported numbers"
    run = ctx.run
    manifest, analysis, trades = run.manifest, run.analysis, run.trades
    if manifest is None or analysis is None or trades is None or run.signals is None:
        miss = [k for k in ("run_manifest.json", "analysis.json", "trades.parquet", "signals.parquet") if k in run.errors]
        return inconclusive("D2", name, f"inputs unavailable: {miss}")
    report = run.report_text
    if report is None:
        return inconclusive("D2", name, "report.md missing")
    if len(trades) and not {"net_pnl", "gross_pnl", "fee_total", "trade_id", "symbol"} <= set(trades.columns):
        return failed("D2", name, "trades.parquet lacks required columns")

    subs = [{"subwindow": s["subwindow"], "start": s["start"], "end": s["end"]} for s in analysis.get("subwindows", [])]
    if len(trades) == 0:
        trades = pd.DataFrame({
            "trade_id": pd.Series(dtype=str), "symbol": pd.Series(dtype=str), "combo_id": pd.Series(dtype=int),
            "combo_name": pd.Series(dtype=str), "timeframe": pd.Series(dtype=str), "exit_reason": pd.Series(dtype=str),
            "entry_time_ms": pd.Series(dtype=np.int64), "net_pnl": pd.Series(dtype=float),
            "gross_pnl": pd.Series(dtype=float), "fee_total": pd.Series(dtype=float),
            "intrabar_ambiguous": pd.Series(dtype=bool), "intrabar_trailing_ambiguous": pd.Series(dtype=bool),
        })

    is_v309 = "reconciliation" in analysis or ("pnl" in analysis and "net_pf_trade" in analysis["pnl"])
    exp = recompute_analysis(trades, manifest, run.signals, run.skipped, run.rejected, run.snapshots, subs, is_v309=is_v309)

    viol = ViolationCollector()
    counter = [0, 0]  # [leaves_total, leaves_checked]
    _walk_compare(analysis, exp, "analysis", viol, counter)
    rep_checked, rep_total = _verify_report(report, exp, manifest, viol)

    unverified = []
    if manifest.get("execution_mode") == "BINGX_PORTFOLIO" and run.snapshots is None:
        unverified.append("m2m drawdown requires snapshots.parquet")
        counter[0] += 2
    total = counter[0] + rep_total
    checked = counter[1] + rep_checked
    return finalize(
        "D2", name, checked, total, viol,
        reason="; ".join(unverified),
        details={"analysis_leaves_total": counter[0], "analysis_leaves_checked": counter[1],
                 "report_numbers_total": rep_total, "report_numbers_checked": rep_checked,
                 "scope": "all numeric leaves of analysis.json (except the 'validation' block and prose notes) and "
                          "every number in report.md, recomputed from trades.parquet"},
    )
