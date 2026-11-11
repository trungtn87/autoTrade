"""Gate D1 (artifact integrity) and D2 (reported numbers recomputed from the ledger).

D2 is a *minimal, independent* report verifier (the V3-09 ``tools/verify_report.py`` is not part of
the V3-08 base this PR branches from). It recomputes every headline statistic of ``analysis.json``
and ``report.md`` from ``trades.parquet`` (+ skipped/rejected/snapshots) and compares; it never reads
a number from ``analysis.json`` in order to compare it with itself.
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
# D2
# ---------------------------------------------------------------------------------------------

def _raw(x: Any, nd: int = 0) -> Any:
    """Keep full precision: comparison tolerance (not rounding) models the report format."""
    return x


def _pf(wins: float, losses: float) -> float | str:
    if losses > 1e-9:
        return _raw(wins / losses, 4)
    return "UNDEFINED_NO_LOSS" if wins > 0 else 0.0


def _profit_factors(df: pd.DataFrame) -> tuple[float | str, float | str]:
    n = df["net_pnl"].to_numpy(dtype=float)
    g = df["gross_pnl"].to_numpy(dtype=float)
    net = _pf(float(n[n > 0].sum()), float(np.abs(n[n < 0]).sum()))
    gross = _pf(float(g[g > 0].sum()), float(np.abs(g[g < 0]).sum()))
    return net, gross


def _r2(x: float | str) -> float | str:
    return x if isinstance(x, str) else _raw(x, 2)


def _group_stats(df: pd.DataFrame) -> dict[str, Any]:
    net_pf, gross_pf = _profit_factors(df)
    legs = len(df)
    return {
        "legs": legs,
        "entries": int(df["trade_id"].nunique()),
        "wins": int((df["net_pnl"] > 0).sum()),
        "gross_pnl": _raw(float(df["gross_pnl"].sum()), 4),
        "fees": _raw(float(df["fee_total"].sum()), 4),
        "net_pnl": _raw(float(df["net_pnl"].sum()), 4),
        "win_rate_pct": _raw((df["net_pnl"] > 0).sum() / legs * 100.0, 2) if legs else 0.0,
        "profit_factor": _r2(net_pf), "net_profit_factor": _r2(net_pf), "gross_profit_factor": _r2(gross_pf),
    }


def recompute_analysis(
    trades: pd.DataFrame,
    manifest: dict[str, Any],
    n_signals: int,
    skipped: pd.DataFrame | None,
    rejected: pd.DataFrame | None,
    snapshots: pd.DataFrame | None,
    subwindows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Independent re-derivation of the analysis structure from the ledger."""
    mode = str(manifest.get("execution_mode", ""))
    initial = float(manifest.get("initial_equity", 0.0))
    t = trades.copy()
    legs = len(t)
    gross = float(t["gross_pnl"].sum())
    fees = float(t["fee_total"].sum())
    net = float(t["net_pnl"].sum())
    net_pf, gross_pf = _profit_factors(t)
    trade_pnl = t.groupby("trade_id")["net_pnl"].sum() if legs else pd.Series(dtype=float)
    n_trades = len(trade_pnl)
    skipped_n = 0 if skipped is None else int((skipped.get("reason", pd.Series(dtype=str)) == "SKIPPED_BY_CONCURRENCY").sum())
    other_skipped = (0 if skipped is None else len(skipped)) - skipped_n
    rej_n = 0 if rejected is None else int((rejected.get("reason", pd.Series(dtype=str)) == "INSUFFICIENT_MARGIN").sum())
    other_rej = (0 if rejected is None else len(rejected)) - rej_n

    # per-symbol
    symbols: dict[str, Any] = {}
    for sym, g in t.groupby("symbol", sort=True):
        npf, gpf = _profit_factors(g)
        symbols[str(sym)] = {
            "symbol": str(sym),
            "entries": int(g["trade_id"].nunique()), "legs": len(g),
            "leg_win_rate_pct": _raw((g["net_pnl"] > 0).sum() / len(g) * 100.0, 2),
            "gross_pnl": _raw(float(g["gross_pnl"].sum()), 4),
            "total_fees": _raw(float(g["fee_total"].sum()), 4),
            "net_pnl": _raw(float(g["net_pnl"].sum()), 4),
            "profit_factor": _r2(npf), "net_profit_factor": _r2(npf), "gross_profit_factor": _r2(gpf),
        }
    sym_reconciled = math.isclose(sum(s["net_pnl"] for s in symbols.values()), _raw(net, 4), abs_tol=1e-3)
    balance_ok = math.isclose(gross - fees, net, abs_tol=1e-4)

    # closed-trade drawdown in ledger order
    cum = np.cumsum(t["net_pnl"].to_numpy(dtype=float))
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
            "m2m_max_drawdown_dollars": _raw(float(dd.max()), 4),
            "m2m_max_drawdown_pct": _raw(float((dd / np.where(pk > 0, pk, np.nan) * 100.0).max()), 2),
        }

    exit_reasons: dict[str, Any] = {}
    for reason, g in t.groupby("exit_reason", sort=False):
        exit_reasons[str(reason)] = {
            "count": len(g), "gross_pnl": _raw(float(g["gross_pnl"].sum()), 4),
            "net_pnl": _raw(float(g["net_pnl"].sum()), 4),
            "pct_of_legs": _raw(len(g) / legs * 100.0, 2) if legs else 0.0,
        }

    combos = []
    for cid, g in t.groupby("combo_id", sort=True):
        s = _group_stats(g)
        combos.append({"combo_id": int(cid), "combo_name": str(g["combo_name"].iloc[0]),
                       "symbol": str(g["symbol"].iloc[0]), "timeframe": str(g["timeframe"].iloc[0]), **s})

    ym = pd.to_datetime(t["entry_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m") if legs else pd.Series(dtype=str)
    monthly = []
    for key, g in t.groupby(ym, sort=True):
        s = _group_stats(g)
        monthly.append({"year_month": str(key), **s})

    sub_out = []
    for sw in subwindows:
        ws = int(pd.Timestamp(sw["start"], tz="UTC").value // 1_000_000)
        we = int(pd.Timestamp(sw["end"], tz="UTC").value // 1_000_000)
        g = t[(t["entry_time_ms"] >= ws) & (t["entry_time_ms"] < we)]
        npf, gpf = _profit_factors(g)
        sub_out.append({
            "subwindow": sw["subwindow"], "start": sw["start"], "end": sw["end"],
            "entries": int(g["trade_id"].nunique()), "legs": len(g),
            "win_rate_pct": _raw((g["net_pnl"] > 0).sum() / len(g) * 100.0, 2) if len(g) else 0.0,
            "gross_pnl": _raw(float(g["gross_pnl"].sum()), 4), "total_fees": _raw(float(g["fee_total"].sum()), 4),
            "net_pnl": _raw(float(g["net_pnl"].sum()), 4),
            "profit_factor": _r2(npf), "net_profit_factor": _r2(npf), "gross_profit_factor": _r2(gpf),
        })

    wl = int((t["net_pnl"] > 0).sum())
    return {
        "mode": mode,
        "accounting": {
            "total_signals": int(n_signals), "accepted_entries": n_trades, "total_legs": legs,
            "skipped_by_concurrency": skipped_n, "other_skipped": other_skipped,
            "rejected_by_margin": rej_n, "other_rejected": other_rej,
            "symbol_reconciliation_pass": sym_reconciled, "accounting_balance_pass": balance_ok,
        },
        "win_rate": {
            "leg_win_rate_pct": _raw(wl / legs * 100.0, 2) if legs else 0.0,
            "trade_win_rate_pct": _raw(int((trade_pnl > 0).sum()) / n_trades * 100.0, 2) if n_trades else 0.0,
            "winning_legs": wl, "losing_legs": int((t["net_pnl"] < 0).sum()),
            "breakeven_legs": int((t["net_pnl"] == 0).sum()),
            "winning_trades": int((trade_pnl > 0).sum()), "losing_trades": int((trade_pnl < 0).sum()),
            "breakeven_trades": int((trade_pnl == 0).sum()),
        },
        "pnl": {
            "gross_pnl": _raw(gross, 4), "total_fees": _raw(fees, 4), "net_pnl": _raw(net, 4),
            "profit_factor": _r2(net_pf), "net_profit_factor": _r2(net_pf), "gross_profit_factor": _r2(gross_pf),
            "roi_pct": _raw(net / initial * 100.0, 2) if initial else 0.0,
            "roi_denominator_value": initial,
            "avg_trade_pnl": _raw(net / n_trades, 4) if n_trades else 0.0,
            "avg_leg_pnl": _raw(net / legs, 4) if legs else 0.0,
            "accounting_verified": bool(balance_ok and sym_reconciled),
        },
        "risk": {
            "closed_trade_max_drawdown_dollars": _raw(max_dd, 4),
            "closed_trade_max_drawdown_pct": _raw(max_dd_pct, 2), **m2m,
        },
        "ambiguity": {
            "intrabar_tp_sl_ambiguous_count": int(t["intrabar_ambiguous"].sum()),
            "intrabar_trailing_ambiguous_count": int(t["intrabar_trailing_ambiguous"].sum()),
        },
        "symbols": symbols,
        "combos": combos,
        "monthly": monthly,
        "subwindows": sub_out,
        "exit_reasons": exit_reasons,
    }


_TEXT_KEYS = {"roi_note", "m2m_note", "m2m_status", "resolution_policy", "roi_denominator_name", "validation"}


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
            if k not in actual:
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
    if "pct" in last or "profit_factor" in last:
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
        tol += 5e-5  # analyzer rounds profit factors twice (4 then 2 decimals)
    return abs(float(actual) - float(exp)) <= tol


_NUM = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z_])")


def _tok_value(tok: str) -> tuple[float, int]:
    clean = tok.replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    dec = len(clean.split(".")[1]) if "." in clean else 0
    return float(clean), dec


def _fmt_matches(tok: str, expected: float) -> bool:
    val, dec = _tok_value(tok)
    return abs(expected - val) <= 0.5 * 10 ** (-dec) + 5e-5 + 1e-9


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
            check_tokens(line.split(":", 1)[1], "gross_pnl", [pnl["gross_pnl"]])
        elif line.startswith("- Total Fees"):
            check_tokens(line, "total_fees", [fee_pct, pnl["total_fees"]])
        elif line.startswith("- Net PnL:"):
            check_tokens(line.split(":", 1)[1], "net_pnl", [pnl["net_pnl"]])
        elif line.startswith("- ROI ("):
            check_tokens(line, "roi", [pnl["roi_denominator_value"], pnl["roi_pct"]])
        elif line.startswith("- Net Profit Factor"):
            v = pnl["net_profit_factor"]
            if isinstance(v, str):
                check_text("net_pf", line.split(":", 1)[1].strip(), v)
            else:
                check_tokens(line.split(":", 1)[1], "net_pf", [v])
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
        elif line.startswith("|") and line.count("|") >= 8:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[0] in sym_rows:
                s = sym_rows[cells[0]]
                nums = _NUM.findall(" ".join(cells[1:5]))
                exp_vals: list[Any] = [s["entries"], s["legs"], s["net_pnl"], s["leg_win_rate_pct"]]
                total += len(nums)
                if len(nums) != 4:
                    viol.add({"line": cells[0], "kind": "TOKEN_COUNT"})
                else:
                    for tok, e in zip(nums, exp_vals):
                        if _fmt_matches(tok, float(e)):
                            checked += 1
                        else:
                            viol.add({"line": cells[0], "kind": "REPORT_NUMBER_MISMATCH", "token": tok, "expected": e})
                for cell, e in ((cells[5], s["net_profit_factor"]), (cells[6], s["gross_profit_factor"])):
                    if isinstance(e, str):
                        check_text(f"{cells[0]}.pf", cell, e)
                    else:
                        check_tokens(cell, f"{cells[0]}.pf", [e])
            elif _NUM.findall(line) and not set(cells[0]) <= set(":-"):
                if cells[0] not in ("Symbol", "Gate"):
                    total += len(_NUM.findall(line))
        else:
            leftover = _NUM.findall(re.sub(r"^#+\s*\d+\.\s*", "", line))
            # section titles "## 1. Accounting" carry an ordinal, which is not a metric
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
    exp = recompute_analysis(trades, manifest, len(run.signals), run.skipped, run.rejected, run.snapshots, subs)

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


