"""Execution gates C1-C5, computed from the saved ledger and the M1 data only."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.validation.context import (
    M1_MS,
    M1Series,
    VerifyContext,
    parse_policy_cell,
    to_ms,
    canon_symbol,
)
from autotrade_v3.validation.hashing import policy_hash
from autotrade_v3.validation.replay import LegSpec, apply_slippage, levels, replay_leg
from autotrade_v3.validation.types import (
    GateResult,
    ViolationCollector,
    failed,
    finalize,
    inconclusive,
)

POLICY_KEYS = (
    "tp_pct", "sl_pct", "split_legs", "trailing_act_pct", "trailing_cb_pct",
    "leg_weights", "max_bars_held", "force_close_on_window_end",
)
LEDGER_COLS = (
    "signal_id", "trade_id", "leg_id", "symbol", "combo_id", "side", "leg_type", "entry_time_ms", "entry_price",
    "exit_time_ms", "exit_price", "exit_reason", "qty", "notional", "gross_pnl", "fee_total", "net_pnl",
    "return_pct",
)


# ------------------------------------------------------------------------------------------
# shared preparation
# ------------------------------------------------------------------------------------------

def _inputs(ctx: VerifyContext, gate_id: str, name: str) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame] | GateResult:
    m, s, t = ctx.run.manifest, ctx.run.signals, ctx.run.trades
    if m is None or s is None or t is None:
        miss = sorted(k for k in ("run_manifest.json", "signals.parquet", "trades.parquet") if k in ctx.run.errors)
        return inconclusive(gate_id, name, f"required inputs unavailable: {miss}")
    if len(t) and not set(LEDGER_COLS) <= set(t.columns):
        return failed(gate_id, name, f"trades.parquet missing columns {sorted(set(LEDGER_COLS) - set(t.columns))}")
    return m, s, t


def _window(series: M1Series, manifest: dict[str, Any]) -> M1Series:
    tw = manifest.get("time_window") or {}
    lo, hi = to_ms(tw["start"]), to_ms(tw["end"])
    keep = (series.t >= lo) & (series.t < hi)
    return M1Series(*(a[keep] for a in (series.t, series.o, series.h, series.l, series.c, series.v, series.provider)))


def _series(ctx: VerifyContext, symbol: str, manifest: dict[str, Any]) -> M1Series:
    key = f"win:{canon_symbol(symbol)}"
    if key not in ctx.cache:
        store = ctx.m1_store()
        assert store is not None
        ctx.cache[key] = _window(store.get(symbol), manifest)
    return ctx.cache[key]


def _m1_available(ctx: VerifyContext) -> str:
    """Return '' when M1 data can be read, else the BLOCKED reason."""
    dm = ctx.run.dataset_manifest
    if not dm:
        return "dataset_manifest.json missing"
    parts = dm.get("partitions", [])
    if parts and not any((ctx.data_root / p["relative_path"]).exists() for p in parts):
        return f"BLOCKED: M1 data not present under {ctx.data_root}"
    return ""


def _strategy_policies(ctx: VerifyContext, manifest: dict[str, Any], trades: pd.DataFrame) -> dict[tuple[str, int], dict[str, Any]] | str:
    """ExitPolicy of the strategy for every (symbol, combo) of the ledger, or an error string."""
    if "policies" in ctx.cache:
        return ctx.cache["policies"]
    try:
        from autotrade_v3.strategy.registry import StrategyRegistry  # lazy: only strategy code, no simulator

        strat = StrategyRegistry.get(str(manifest.get("strategy_id")))
        out: dict[tuple[str, int], dict[str, Any]] = {}
        for sym, cid in sorted({(str(a), int(b)) for a, b in zip(trades["symbol"], trades["combo_id"])}):
            p = strat.get_exit_policy(sym, cid)
            out[(sym, cid)] = {k: getattr(p, k) for k in POLICY_KEYS}
        ctx.cache["policies"] = out
        return out
    except Exception as e:  # noqa: BLE001
        return f"strategy ExitPolicy unavailable: {type(e).__name__}: {e}"


def _entry_arrays(trades: pd.DataFrame, signals: pd.DataFrame, ctx: VerifyContext, manifest: dict[str, Any]) -> dict[str, np.ndarray]:
    """Per ledger row: signal close, entry bar index and the M1 open at that bar (NaN when unresolved)."""
    n = len(trades)
    sig_close = signals.drop_duplicates("signal_id").set_index("signal_id")["signal_close_ms"]
    sc = trades["signal_id"].map(sig_close).to_numpy(dtype=float)
    idx = np.full(n, -1, dtype=np.int64)
    open_px = np.full(n, np.nan)
    bar_t = np.full(n, -1, dtype=np.int64)
    syms = trades["symbol"].to_numpy()
    for sym in sorted(set(syms.tolist())):
        ser = _series(ctx, str(sym), manifest)
        rows = np.nonzero((syms == sym) & ~np.isnan(sc))[0]
        if not len(rows):
            continue
        k = np.searchsorted(ser.t, sc[rows].astype(np.int64) + 1, side="left")
        ok = k < len(ser.t)
        good = rows[ok]
        idx[good] = k[ok]
        open_px[good] = ser.o[k[ok]]
        bar_t[good] = ser.t[k[ok]]
    return {"sc": sc, "idx": idx, "open": open_px, "bar_t": bar_t}


# ------------------------------------------------------------------------------------------
# Mode C VWAP reconstruction (used by C1)
# ------------------------------------------------------------------------------------------

def _vwap_entries(trades: pd.DataFrame, ent: dict[str, np.ndarray], ctx: VerifyContext, manifest: dict[str, Any],
                  slip: float) -> np.ndarray:
    """Re-derive the blended (VWAP) entry price the portfolio ledger reports for each leg at its exit."""
    n = len(trades)
    out = np.full(n, np.nan)
    side_buy = (trades["side"].to_numpy() == "BUY")
    own = np.array([apply_slippage(float(o), bool(b), True, slip) if not np.isnan(o) else np.nan
                    for o, b in zip(ent["open"], side_buy)])
    exit_idx = np.full(n, -1, dtype=np.int64)
    syms = trades["symbol"].to_numpy()
    for sym in sorted(set(syms.tolist())):
        ser = _series(ctx, str(sym), manifest)
        rows = np.nonzero(syms == sym)[0]
        et = trades["exit_time_ms"].to_numpy(dtype=np.int64)[rows]
        k = np.searchsorted(ser.t, et)
        okk = (k < len(ser.t))
        okk[okk] = ser.t[k[okk]] == et[okk]
        exit_idx[rows[okk]] = k[okk]

    notional_trade = trades.groupby("trade_id")["notional"].transform("sum").to_numpy(dtype=float)
    first_of_trade = ~trades.duplicated("trade_id").to_numpy()
    combo = trades["combo_id"].to_numpy()
    events: dict[tuple[str, str], list[tuple]] = {}
    for i in range(n):
        if np.isnan(own[i]) or ent["idx"][i] < 0:
            continue
        key = (str(syms[i]), "BUY" if side_buy[i] else "SELL")
        if first_of_trade[i]:
            events.setdefault(key, []).append((int(ent["idx"][i]), 0, (float(ent["sc"][i]), int(combo[i])), i))
        if exit_idx[i] >= 0:
            events.setdefault(key, []).append((int(exit_idx[i]), 1, (float(i), 0), i))
    for key, evs in events.items():
        evs.sort(key=lambda e: (e[0], e[1], e[2]))
        qty = 0.0
        avg = 0.0
        for _bar, kind, _k, i in evs:
            if kind == 0:
                tot_qty = notional_trade[i] / own[i]
                new_qty = qty + tot_qty
                if new_qty > 1e-9:
                    avg = (avg * qty + notional_trade[i]) / new_qty
                qty = new_qty
            else:
                leg_qty = float(trades["notional"].iat[i]) / own[i]
                out[i] = avg if avg > 0 else own[i]
                qty = max(0.0, qty - leg_qty)
                if qty <= 1e-9:
                    qty = 0.0
                    avg = 0.0
    return out


# ------------------------------------------------------------------------------------------
# C1 causality
# ------------------------------------------------------------------------------------------

def gate_c1(ctx: VerifyContext) -> GateResult:
    name = "Entry causality"
    got = _inputs(ctx, "C1", name)
    if isinstance(got, GateResult):
        return got
    manifest, signals, trades = got
    n = len(trades)
    if n == 0:
        return finalize("C1", name, 0, 0, ViolationCollector(), details={"note": "ledger has no legs"})
    blocked = _m1_available(ctx)
    if blocked:
        return inconclusive("C1", name, blocked, total=n)
    slip = float(manifest.get("slippage_rate", 0.0))
    ent = _entry_arrays(trades, signals, ctx, manifest)
    viol = ViolationCollector()
    sid = trades["signal_id"].to_numpy()
    lid = trades["leg_id"].to_numpy()
    et = trades["entry_time_ms"].to_numpy(dtype=np.int64)
    ep = trades["entry_price"].to_numpy(dtype=float)
    qty = trades["qty"].to_numpy(dtype=float)
    notional = trades["notional"].to_numpy(dtype=float)
    buy = trades["side"].to_numpy() == "BUY"
    portfolio = ctx.mode == "BINGX_PORTFOLIO"

    unknown = np.isnan(ent["sc"])
    for i in np.nonzero(unknown)[0]:
        viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "UNKNOWN_SIGNAL_ID"})
    nobar = (~unknown) & (ent["idx"] < 0)
    for i in np.nonzero(nobar)[0]:
        viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "NO_ENTRY_BAR_IN_DATA"})
    ok = (~unknown) & (ent["idx"] >= 0)
    bad_t = ok & (et != ent["bar_t"])
    for i in np.nonzero(bad_t)[0]:
        viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "ENTRY_TIME", "expected_ms": int(ent["bar_t"][i]),
                  "actual_ms": int(et[i]), "delta_min": (int(et[i]) - int(ent["bar_t"][i])) / M1_MS})
    own = np.where(buy, ent["open"] * (1.0 + slip), ent["open"] * (1.0 - slip)) if slip > 0 else ent["open"]
    if not portfolio:
        bad_p = ok & (np.abs(ep - own) > (2e-6 + 1e-9 * np.abs(own)))
        for i in np.nonzero(bad_p)[0]:
            viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "ENTRY_PRICE",
                      "expected": float(own[i]), "actual": float(ep[i])})
    else:
        # ledger qty is rounded to 6 decimals; the fill must satisfy qty * fill == notional within that rounding
        bad_q = ok & (np.abs(qty * own - notional) > (5.1e-7 * np.abs(own) + 1e-6))
        for i in np.nonzero(bad_q)[0]:
            viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "FILL_QTY_NOTIONAL",
                      "open_fill": float(own[i]), "qty": float(qty[i]), "notional": float(notional[i])})
        vwap = _vwap_entries(trades, ent, ctx, manifest, slip)
        bad_v = ok & (np.isnan(vwap) | (np.abs(ep - vwap) > (2e-6 + 1e-9 * np.abs(ep))))
        for i in np.nonzero(bad_v)[0]:
            viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "VWAP_ENTRY_PRICE",
                      "expected": None if np.isnan(vwap[i]) else float(vwap[i]), "actual": float(ep[i])})
    return finalize("C1", name, n, n, viol, details={"mode": ctx.mode, "price_check": "VWAP-reconstruction" if portfolio else "open+slippage"})


# ------------------------------------------------------------------------------------------
# C2 ExitPolicy propagation
# ------------------------------------------------------------------------------------------

def _none_or_eq(a: Any, b: Any) -> bool:
    a_none = a is None or (isinstance(a, float) and math.isnan(a))
    b_none = b is None or (isinstance(b, float) and math.isnan(b))
    if a_none or b_none:
        return a_none and b_none
    return float(a) == float(b)


def gate_c2(ctx: VerifyContext) -> GateResult:
    name = "ExitPolicy propagation"
    got = _inputs(ctx, "C2", name)
    if isinstance(got, GateResult):
        return got
    manifest, signals, trades = got
    n = len(trades)
    if n == 0:
        return finalize("C2", name, 0, 0, ViolationCollector(), details={"note": "ledger has no legs"})
    pol = _strategy_policies(ctx, manifest, trades)
    if isinstance(pol, str):
        return inconclusive("C2", name, pol, total=n)
    base_notional = float(manifest.get("notional_per_trade", 0.0))
    sig_policy = signals.drop_duplicates("signal_id").set_index("signal_id")
    viol = ViolationCollector()
    portfolio = ctx.mode == "BINGX_PORTFOLIO"
    cols = {c: trades[c].to_numpy() for c in trades.columns if c in LEDGER_COLS or c in (
        "tp_pct", "sl_pct", "trailing_act_pct", "trailing_cb_pct", "max_bars_held", "exit_policy_hash")}
    for i in range(n):
        lid, sid = cols["leg_id"][i], cols["signal_id"][i]
        p = pol[(str(cols["symbol"][i]), int(cols["combo_id"][i]))]
        want_hash = policy_hash(p)
        ex = lambda kind, **kw: viol.add({"leg_id": lid, "signal_id": sid, "kind": kind, **kw})  # noqa: E731
        for led, key in (("tp_pct", "tp_pct"), ("sl_pct", "sl_pct"), ("trailing_act_pct", "trailing_act_pct"),
                         ("trailing_cb_pct", "trailing_cb_pct")):
            if led in cols and not _none_or_eq(cols[led][i], p[key]):
                ex("POLICY_FIELD", field=led, ledger=float(cols[led][i]), strategy=float(p[key]))
        if "max_bars_held" in cols and not _none_or_eq(cols["max_bars_held"][i], p["max_bars_held"]):
            ex("POLICY_FIELD", field="max_bars_held", ledger=str(cols["max_bars_held"][i]), strategy=str(p["max_bars_held"]))
        if cols.get("exit_policy_hash") is not None and cols["exit_policy_hash"][i] != want_hash:
            ex("POLICY_HASH_LEDGER", ledger=str(cols["exit_policy_hash"][i]), recomputed=want_hash)
        # leg structure and size
        weights = list(p["leg_weights"])
        if p["split_legs"]:
            expected = {"TP_HALF": weights[0] if weights else 0.5, "TRAIL_HALF": weights[1] if len(weights) > 1 else 0.5}
        else:
            expected = {"SINGLE": weights[0] if weights else 1.0}
        lt = str(cols["leg_type"][i])
        if lt not in expected:
            ex("LEG_TYPE", leg_type=lt, allowed=sorted(expected))
        elif abs(float(cols["notional"][i]) - base_notional * expected[lt]) > 1e-9 * max(1.0, base_notional):
            ex("LEG_NOTIONAL", expected=base_notional * expected[lt], actual=float(cols["notional"][i]))
        # recomputed levels vs the exit actually recorded
        entry = float(cols["entry_price"][i]) if not portfolio else float(cols["notional"][i]) / float(cols["qty"][i])
        tol = abs(entry) * (8e-4 if portfolio else 1e-9) + 2e-4
        buy = cols["side"][i] == "BUY"
        spec = LegSpec(buy, "FIXED", 0, entry, p["tp_pct"], p["sl_pct"], p["trailing_act_pct"], p["trailing_cb_pct"])
        tp, sl, act = levels(spec)
        reason, xp = str(cols["exit_reason"][i]), float(cols["exit_price"][i])
        slip = float(manifest.get("slippage_rate", 0.0))
        def xs(price: float) -> float:
            return apply_slippage(price, bool(buy), False, slip)
        if reason == "TP" and ((buy and xp < xs(tp) - tol) or (not buy and xp > xs(tp) + tol)):
            ex("TP_PRICE", exit_price=xp, tp_level=xs(tp))
        if reason in ("SL", "SL_INTRABAR_AMBIGUOUS") and ((buy and xp > xs(sl) + tol) or (not buy and xp < xs(sl) - tol)):
            ex("SL_PRICE", exit_price=xp, sl_level=xs(sl))
        if reason == "SL_INTRABAR_AMBIGUOUS" and abs(xp - xs(sl)) > tol:
            ex("AMBIGUOUS_NOT_AT_SL", exit_price=xp, sl_level=xs(sl))
        if reason == "TRAIL" and ((buy and xp < xs(sl) - tol) or (not buy and xp > xs(sl) + tol)):
            ex("TRAIL_BELOW_SL", exit_price=xp, sl_level=xs(sl))
        if reason == "TRAIL" and ((buy and xp < xs(entry) * 0.0) or lt != "TRAIL_HALF"):
            if lt != "TRAIL_HALF":
                ex("TRAIL_ON_NON_TRAIL_LEG", leg_type=lt)
        # signal-side copies of the policy
        if sid in sig_policy.index:
            row = sig_policy.loc[sid]
            if "exit_policy_hash" in sig_policy.columns and row["exit_policy_hash"] != want_hash:
                ex("POLICY_HASH_SIGNAL", signal=str(row["exit_policy_hash"]), recomputed=want_hash)
            sp = parse_policy_cell(row.get("exit_policy")) if "exit_policy" in sig_policy.columns else None
            if sp is not None:
                for key in POLICY_KEYS:
                    a, b = sp.get(key), p[key]
                    same = (list(a) == list(b)) if isinstance(b, (tuple, list)) else (
                        _none_or_eq(a, b) if not isinstance(b, bool) else a == b)
                    if not same:
                        ex("POLICY_SIGNAL_COPY", field=key, signal=str(a), strategy=str(b))
                        break
        else:
            ex("UNKNOWN_SIGNAL_ID")
    # leg count per signal
    legs_per_sig = trades.groupby("signal_id").size()
    for sid_, cnt in legs_per_sig.items():
        row_i = int(np.nonzero(cols["signal_id"] == sid_)[0][0])
        p = pol[(str(cols["symbol"][row_i]), int(cols["combo_id"][row_i]))]
        want = 2 if p["split_legs"] else 1
        if int(cnt) != want:
            viol.add({"signal_id": sid_, "kind": "LEG_COUNT", "expected": want, "actual": int(cnt)})
    return finalize("C2", name, n, n, viol, details={"policies_checked": len(pol)})


# ------------------------------------------------------------------------------------------
# C3 exit replay
# ------------------------------------------------------------------------------------------

def gate_c3(ctx: VerifyContext) -> GateResult:
    name = "Exit replay"
    got = _inputs(ctx, "C3", name)
    if isinstance(got, GateResult):
        return got
    manifest, signals, trades = got
    n = len(trades)
    if n == 0:
        return finalize("C3", name, 0, 0, ViolationCollector(), details={"note": "ledger has no legs"})
    blocked = _m1_available(ctx)
    if blocked:
        return inconclusive("C3", name, blocked, total=n)
    pol = _strategy_policies(ctx, manifest, trades)
    if isinstance(pol, str):
        return inconclusive("C3", name, pol, total=n)
    slip = float(manifest.get("slippage_rate", 0.0))
    portfolio = ctx.mode == "BINGX_PORTFOLIO"
    ent = _entry_arrays(trades, signals, ctx, manifest)
    viol = ViolationCollector()
    sym = trades["symbol"].to_numpy()
    cid = trades["combo_id"].to_numpy()
    side = trades["side"].to_numpy()
    ltype = trades["leg_type"].to_numpy()
    lid = trades["leg_id"].to_numpy()
    sid = trades["signal_id"].to_numpy()
    x_t = trades["exit_time_ms"].to_numpy(dtype=np.int64)
    x_p = trades["exit_price"].to_numpy(dtype=float)
    x_r = trades["exit_reason"].to_numpy()
    held = trades["bars_held"].to_numpy(dtype=np.int64) if "bars_held" in trades.columns else None
    f_amb = trades["intrabar_ambiguous"].to_numpy(dtype=bool) if "intrabar_ambiguous" in trades.columns else None
    f_trl = trades["intrabar_trailing_ambiguous"].to_numpy(dtype=bool) if "intrabar_trailing_ambiguous" in trades.columns else None
    mism: list[dict[str, Any]] = []
    stats = {"replayed": 0}
    for i in range(n):
        ex_idx = int(ent["idx"][i])
        if ex_idx < 0:
            viol.add({"leg_id": lid[i], "signal_id": sid[i], "kind": "NO_ENTRY_BAR"})
            continue
        ser = _series(ctx, str(sym[i]), manifest)
        p = pol[(str(sym[i]), int(cid[i]))]
        buy = side[i] == "BUY"
        fill = apply_slippage(float(ser.o[ex_idx]), bool(buy), True, slip)
        kind = "TRAIL" if ltype[i] == "TRAIL_HALF" else "FIXED"
        spec = LegSpec(bool(buy), kind, ex_idx, fill, p["tp_pct"], p["sl_pct"], p["trailing_act_pct"],
                       p["trailing_cb_pct"], p["max_bars_held"], gap_on_entry=portfolio, force_final=portfolio)
        r = replay_leg(ser.o, ser.h, ser.l, ser.c, spec)
        stats["replayed"] += 1
        exp_t = int(ser.t[r.idx])
        exp_px = apply_slippage(r.raw_price, bool(buy), False, slip)
        problems = []
        if exp_t != int(x_t[i]):
            problems.append("EXIT_TIME")
        if r.reason != str(x_r[i]):
            problems.append("EXIT_REASON")
        if abs(exp_px - float(x_p[i])) > 1e-4:
            problems.append("EXIT_PRICE")
        if held is not None and r.idx - ex_idx != int(held[i]):
            problems.append("BARS_HELD")
        if f_amb is not None and r.amb_tpsl != bool(f_amb[i]):
            problems.append("AMBIGUOUS_FLAG")
        if f_trl is not None and r.amb_trail != bool(f_trl[i]):
            problems.append("TRAILING_AMBIGUOUS_FLAG")
        if problems:
            rec = {
                "leg_id": lid[i], "signal_id": sid[i], "symbol": sym[i], "combo_id": int(cid[i]),
                "leg_type": ltype[i], "problems": ",".join(problems),
                "ledger_exit_time_ms": int(x_t[i]), "replay_exit_time_ms": exp_t,
                "ledger_exit_reason": str(x_r[i]), "replay_exit_reason": r.reason,
                "ledger_exit_price": float(x_p[i]), "replay_exit_price": exp_px,
                "ledger_bars_held": None if held is None else int(held[i]), "replay_bars_held": int(r.idx - ex_idx),
            }
            mism.append(rec)
            viol.add({k: rec[k] for k in ("leg_id", "signal_id", "problems", "ledger_exit_reason",
                                          "replay_exit_reason", "ledger_exit_time_ms", "replay_exit_time_ms")})
    res = finalize("C3", name, n, n, viol, details={"mode": ctx.mode, "legs_replayed": stats["replayed"],
                                                    "mismatch_rows": len(mism)})
    ctx.cache["c3_mismatches"] = mism
    return res


# ------------------------------------------------------------------------------------------
# C4 accounting
# ------------------------------------------------------------------------------------------

def gate_c4(ctx: VerifyContext) -> GateResult:
    name = "Accounting"
    run = ctx.run
    manifest, trades = run.manifest, run.trades
    if manifest is None or trades is None:
        return inconclusive("C4", name, f"required inputs unavailable: {sorted(run.errors)}")
    n = len(trades)
    viol = ViolationCollector()
    fee = float(manifest.get("fee_rate_taker", 0.0))
    initial = float(manifest.get("initial_equity", 0.0))
    portfolio = ctx.mode == "BINGX_PORTFOLIO"
    notes: list[str] = []
    if n:
        need = {"leg_id", "entry_price", "exit_price", "qty", "notional", "gross_pnl", "fee_total", "net_pnl", "side", "return_pct"}
        if not need <= set(trades.columns):
            return failed("C4", name, f"trades.parquet missing {sorted(need - set(trades.columns))}")
        e = trades["entry_price"].to_numpy(dtype=float)
        x = trades["exit_price"].to_numpy(dtype=float)
        notional = trades["notional"].to_numpy(dtype=float)
        qty_led = trades["qty"].to_numpy(dtype=float)
        buy = trades["side"].to_numpy() == "BUY"
        # A/B: qty is exactly notional/entry. C: entry is a VWAP blend, so the (rounded) ledger qty is used.
        qty = qty_led if portfolio else notional / e
        dp = np.where(buy, x - e, e - x)
        gross = dp * qty
        fees = notional * fee + qty * x * fee
        net = gross - fees
        ret = net / notional
        slack_q = 5.1e-7 * np.abs(dp) if portfolio else 0.0
        tol_g = 2e-6 + 6e-5 * qty + slack_q
        tol_f = 2e-6 + 6e-5 * qty * fee + (5.1e-7 * x * fee if portfolio else 0.0)
        lid = trades["leg_id"].to_numpy()
        for name_, calc, led, tol in (
            ("gross_pnl", gross, trades["gross_pnl"].to_numpy(dtype=float), tol_g),
            ("fee_total", fees, trades["fee_total"].to_numpy(dtype=float), tol_f),
            ("net_pnl", net, trades["net_pnl"].to_numpy(dtype=float), tol_g + tol_f),
            ("return_pct", ret, trades["return_pct"].to_numpy(dtype=float), (tol_g + tol_f) / notional + 2e-6),
        ):
            bad = np.nonzero(~(np.abs(calc - led) <= tol))[0]
            if len(bad):
                viol.add_many(len(bad), [{"leg_id": lid[i], "kind": f"LEG_{name_.upper()}", "ledger": float(led[i]),
                                          "recomputed": float(calc[i])} for i in bad[:20]])
        # internal consistency of the ledger row
        led_net = trades["net_pnl"].to_numpy(dtype=float)
        led_gross = trades["gross_pnl"].to_numpy(dtype=float)
        led_fee = trades["fee_total"].to_numpy(dtype=float)
        bad = np.nonzero(np.abs(led_gross - led_fee - led_net) > 3e-6)[0]
        if len(bad):
            viol.add_many(len(bad), [{"leg_id": lid[i], "kind": "NET_NE_GROSS_MINUS_FEE"} for i in bad[:20]])

        # leg totals vs execution events (modes A/B)
        ev = run.events
        if ev is not None and len(ev):
            eg = ev.copy()
            ex_ev = eg[eg["event_type"] == "EXIT"].set_index("leg_id")
            en_ev = eg[eg["event_type"] == "ENTRY"].set_index("leg_id")
            tl = trades.set_index("leg_id")
            for leg in tl.index.difference(ex_ev.index).tolist()[:20]:
                viol.add({"leg_id": leg, "kind": "NO_EXIT_EVENT"})
            for leg in ex_ev.index.difference(tl.index).tolist()[:20]:
                viol.add({"leg_id": leg, "kind": "ORPHAN_EVENT"})
            common = tl.index.intersection(ex_ev.index).intersection(en_ev.index)
            t_c, x_c, e_c = tl.loc[common], ex_ev.loc[common], en_ev.loc[common]
            checks = (
                ("EVENT_REALIZED_PNL", (x_c["realized_pnl"].astype(float) - t_c["net_pnl"]).abs() > 3e-6),
                ("EVENT_FEES", ((x_c["fee"].astype(float) + e_c["fee"].astype(float)) - t_c["fee_total"]).abs() > 3e-6),
                ("EVENT_EXIT_PRICE", (x_c["price"].astype(float) - t_c["exit_price"]).abs() > 1e-4),
                ("EVENT_ENTRY_PRICE", (e_c["price"].astype(float) - t_c["entry_price"]).abs() > 1e-4),
                ("EVENT_EXIT_TIME", x_c["timestamp_ms"].astype("int64") != t_c["exit_time_ms"].astype("int64")),
                ("EVENT_ENTRY_TIME", e_c["timestamp_ms"].astype("int64") != t_c["entry_time_ms"].astype("int64")),
                ("EVENT_EXIT_REASON", x_c["exit_reason"].astype(str) != t_c["exit_reason"].astype(str)),
            )
            for kind, mask in checks:
                bad_legs = mask[mask].index.tolist()
                if bad_legs:
                    viol.add_many(len(bad_legs), [{"leg_id": b, "kind": kind} for b in bad_legs[:20]])
            sum_ev = float(ex_ev["realized_pnl"].astype(float).sum())
            sum_leg = float(trades["net_pnl"].sum())
            if abs(sum_ev - sum_leg) > 3e-6 * max(1, n):
                viol.add({"kind": "SUM_LEGS_VS_SUM_EVENTS", "legs": sum_leg, "events": sum_ev})
        elif portfolio:
            notes.append("execution_events.parquet has no rows in portfolio mode: leg-vs-event totals not applicable")
        else:
            viol.add({"kind": "EVENTS_MISSING_OR_EMPTY", "detail": "modes A/B must provide execution_events"})
        if int(manifest.get("total_legs", n)) != n:
            viol.add({"kind": "MANIFEST_TOTAL_LEGS", "manifest": manifest.get("total_legs"), "ledger": n})
    if portfolio:
        snap = run.snapshots
        if snap is None or not len(snap):
            return inconclusive("C4", name, "portfolio mode requires snapshots.parquet to check final equity", total=n)
        final_eq = float(snap["equity"].iloc[-1])
        want = initial + (float(trades["net_pnl"].sum()) if n else 0.0)
        tol = 1e-4 + 2e-7 * n
        if abs(final_eq - want) > tol:
            viol.add({"kind": "FINAL_EQUITY", "snapshot_equity": final_eq, "initial_plus_sum_net": want})
    return finalize("C4", name, n, n, viol, reason="", details={"notes": notes, "mode": ctx.mode})


# ------------------------------------------------------------------------------------------
# C5 signal conservation
# ------------------------------------------------------------------------------------------

def gate_c5(ctx: VerifyContext) -> GateResult:
    name = "Signal conservation"
    run = ctx.run
    signals, trades = run.signals, run.trades
    if signals is None or trades is None or run.skipped is None or run.rejected is None:
        return inconclusive("C5", name, f"required inputs unavailable: {sorted(run.errors)}")
    n = len(signals)
    viol = ViolationCollector()
    ids = signals["signal_id"].astype(str)
    dup = ids[ids.duplicated()].unique().tolist()
    if dup:
        viol.add_many(len(dup), [{"signal_id": d, "kind": "DUPLICATE_SIGNAL_ID"} for d in dup[:20]])
    sig_set = set(ids)
    leg_ids = set(trades["signal_id"].astype(str)) if len(trades) else set()
    skip_ids = list(run.skipped["signal_id"].astype(str)) if len(run.skipped) else []
    rej_ids = list(run.rejected["signal_id"].astype(str)) if len(run.rejected) else []
    for kind, coll in (("SKIPPED", skip_ids), ("REJECTED", rej_ids)):
        d = pd.Series(coll)
        dd = d[d.duplicated()].unique().tolist()
        if dd:
            viol.add_many(len(dd), [{"signal_id": x, "kind": f"{kind}_TWICE"} for x in dd[:20]])
    skip_set, rej_set = set(skip_ids), set(rej_ids)
    status_count = {"ACCEPTED": 0, "SKIPPED": 0, "REJECTED": 0}
    for sid in sorted(sig_set):
        states = [s for s, hit in (("ACCEPTED", sid in leg_ids), ("SKIPPED", sid in skip_set),
                                   ("REJECTED", sid in rej_set)) if hit]
        if len(states) != 1:
            viol.add({"signal_id": sid, "kind": "STATE_COUNT", "states": states})
        else:
            status_count[states[0]] += 1
    for sid in sorted(leg_ids - sig_set):
        viol.add({"signal_id": sid, "kind": "LEG_WITH_UNKNOWN_SIGNAL"})
    for sid in sorted(skip_set - sig_set):
        viol.add({"signal_id": sid, "kind": "SKIPPED_UNKNOWN_SIGNAL"})
    for sid in sorted(rej_set - sig_set):
        viol.add({"signal_id": sid, "kind": "REJECTED_UNKNOWN_SIGNAL"})
    if len(trades):
        per_sig_trade = trades.groupby("signal_id")["trade_id"].nunique()
        for sid, k in per_sig_trade[per_sig_trade != 1].items():
            viol.add({"signal_id": sid, "kind": "SIGNAL_WITH_MULTIPLE_TRADES", "trades": int(k)})
    m = run.manifest or {}
    if ctx.mode == "ALL_SIGNALS" and (skip_ids or rej_ids):
        viol.add({"kind": "MODE_A_MUST_ACCEPT_ALL", "skipped": len(skip_ids), "rejected": len(rej_ids)})
    for key, actual in (("total_signals", n), ("accepted_entries", status_count["ACCEPTED"]),
                        ("skipped_signals_count", len(skip_set)), ("rejected_signals_count", len(rej_set)),
                        ("total_legs", len(trades))):
        if key in m and int(m[key]) != actual:
            viol.add({"kind": "MANIFEST_COUNT", "field": key, "manifest": m[key], "recomputed": actual})
    return finalize("C5", name, n, n, viol, details={"statuses": status_count})
