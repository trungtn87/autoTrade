from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

NOTIONAL = 100.0
FEE = 0.0005
INITIAL_EQUITY = 1000.0
DEFAULT_MMR = 0.004


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def isolated_liq_price(entry: float, leverage: float, side: str, mmr: float) -> float:
    # Proxy calibrated to isolated initial margin:
    # initial_margin + UPNL == maintenance_margin + closing_fee.
    k = mmr + FEE
    if side == "L":
        return entry * (1.0 - 1.0 / leverage) / (1.0 - k)
    return entry * (1.0 + 1.0 / leverage) / (1.0 + k)


def cross_liq_price(balance: float, entry: float, qty: float, side: str, mmr: float):
    # Same account-level proxy used by the prior cross-margin research run.
    # For a $100 position against a ~$1000 wallet this is normally far beyond
    # the locked FINAL14 hard SL, so strategy SL should trigger first.
    k = mmr + FEE
    notional = entry * qty
    if side == "L":
        num = notional - balance
        den = qty * (1.0 - k)
        if den <= 0 or num <= 0:
            return None
        return num / den
    den = qty * (1.0 + k)
    if den <= 0:
        return None
    return (balance + notional) / den


def build_events(prod_root: Path, path: str, symbol: str):
    sys.path.insert(0, str(prod_root / "signal_engine"))
    from app.final14_config import FINAL14_CASES
    from app.final14_research import layer3_long as prod_l3
    from app.final14_research.two_trail_layer12 import approved

    d = prod_l3.load(path)
    pc = prod_l3.precompute(d)
    av = prod_l3.entry_variants(pc)

    lock = {}
    selected = {}
    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = "TIER" if combo == 11 else f"C{combo}"
        lock[cname] = {"layer2_variant": cfg["layer2"]}
        matches = [x for x in av[cname] if x[0] == cfg["entry_variant"]]
        if len(matches) != 1:
            raise RuntimeError((symbol, cname, cfg["entry_variant"], len(matches)))
        selected[cname] = matches

    ctx = prod_l3.build_context(d, lock, selected)
    events = {}
    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = "TIER" if combo == 11 else f"C{combo}"
        raw = ctx[cname][cfg["entry_variant"]]
        events[combo] = [
            (int(pos), side, ot, int(sd), bool(blocked))
            for pos, side, ot, sd, blocked in raw
            if approved(side, sd, blocked, cfg["layer2"])
        ]
    return d, events


def simulate_case(d, events, cfg, start, end, model: str, leverage: float, mmr: float):
    idx = d.index
    a = int(idx.searchsorted(start))
    b = int(idx.searchsorted(end))
    H = d.high.to_numpy(float)
    L = d.low.to_numpy(float)
    C = d.close.to_numpy(float)
    ev = [e for e in events if a <= e[0] < b]

    p = 0
    eq = INITIAL_EQUITY
    peak = eq
    dd = 0.0
    pos_sum = neg_sum = fees = 0.0
    trades = wins = losses = liqs = 0
    rows = []

    while p < len(ev):
        ei, side, ot, sd, blocked = ev[p]
        entry = float(C[ei])
        qty = NOTIONAL / entry
        tp_pct = float(cfg["tp_pct"])
        sl_pct = float(cfg["sl_pct"])
        if side == "L":
            tp = entry * (1.0 + tp_pct)
            sl = entry * (1.0 - sl_pct)
        else:
            tp = entry * (1.0 - tp_pct)
            sl = entry * (1.0 + sl_pct)

        liq = None
        if model.startswith("isolated"):
            liq = isolated_liq_price(entry, leverage, side, mmr)
        elif model == "cross_x100":
            liq = cross_liq_price(eq, entry, qty, side, mmr)

        if side == "L":
            adverse = sl
            adverse_reason = "SL"
            if liq is not None and liq > adverse:
                adverse = liq
                adverse_reason = "LIQ"
        else:
            adverse = sl
            adverse_reason = "SL"
            if liq is not None and liq < adverse:
                adverse = liq
                adverse_reason = "LIQ"

        exit_i = None
        exit_px = None
        reason = None
        # Production/backtest semantics: no exit on entry candle, adverse first
        # when TP and adverse barrier are both touched on the same 15m candle.
        for i in range(ei + 1, b):
            hit_adv = L[i] <= adverse if side == "L" else H[i] >= adverse
            hit_tp = H[i] >= tp if side == "L" else L[i] <= tp
            if hit_adv or hit_tp:
                exit_i = i
                if hit_adv:
                    exit_px = adverse
                    reason = adverse_reason
                else:
                    exit_px = tp
                    reason = "TP"
                break

        if exit_i is None:
            exit_i = b - 1
            exit_px = float(C[exit_i])
            reason = "END_MTM"

        gross = (exit_px - entry) * qty * (1.0 if side == "L" else -1.0)
        trade_fees = NOTIONAL * FEE + exit_px * qty * FEE
        pnl = gross - trade_fees

        eq += pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        fees += trade_fees
        trades += 1
        if pnl > 0:
            wins += 1
            pos_sum += pnl
        elif pnl < 0:
            losses += 1
            neg_sum += -pnl
        if reason == "LIQ":
            liqs += 1

        rows.append({
            "entry_time": str(idx[ei]),
            "exit_time": str(idx[exit_i]),
            "side": side,
            "entry": entry,
            "exit": exit_px,
            "tp": tp,
            "sl": sl,
            "liq_proxy": liq,
            "reason": reason,
            "pnl": pnl,
            "bars_held": int(exit_i - ei),
        })

        p += 1
        while p < len(ev) and ev[p][0] < exit_i:
            p += 1

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades * 100.0 if trades else np.nan,
        "profit_factor": pos_sum / neg_sum if neg_sum else (np.inf if pos_sum else np.nan),
        "net_pnl": eq - INITIAL_EQUITY,
        "fees": fees,
        "max_drawdown_realized": dd,
        "ending_equity": eq,
        "liquidations": liqs,
    }, rows


def portfolio_from_trades(all_rows, leverage):
    exits = []
    sweep = []
    total_trades = wins = losses = liqs = 0
    total_fees_proxy = 0.0
    for key, rows in all_rows.items():
        for r in rows:
            total_trades += 1
            wins += int(r["pnl"] > 0)
            losses += int(r["pnl"] < 0)
            liqs += int(r["reason"] == "LIQ")
            exits.append((pd.Timestamp(r["exit_time"]), float(r["pnl"])))
            # End processed before entry on same timestamp.
            sweep.append((pd.Timestamp(r["entry_time"]), +1))
            sweep.append((pd.Timestamp(r["exit_time"]), -1))

    exits.sort(key=lambda x: x[0])
    eq = INITIAL_EQUITY
    peak = eq
    dd = 0.0
    for _, pnl in exits:
        eq += pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)

    # Sort exits (-1) before entries (+1) on the same candle.
    sweep.sort(key=lambda x: (x[0], x[1]))
    active = peak_active = 0
    for _, delta in sweep:
        active += delta
        peak_active = max(peak_active, active)

    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / total_trades * 100.0 if total_trades else np.nan,
        "net_pnl": eq - INITIAL_EQUITY,
        "ending_equity": eq,
        "max_drawdown_realized": dd,
        "liquidations": liqs,
        "peak_concurrent_positions": peak_active,
        "peak_notional_usdt": peak_active * NOTIONAL,
        "peak_initial_margin_usdt": peak_active * NOTIONAL / leverage,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod-root", required=True)
    ap.add_argument("--prod-ref", required=True)
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    ap.add_argument("--out-dir", default="results/final14_prodlogic_margin_compare")
    ap.add_argument("--mmr", type=float, default=DEFAULT_MMR)
    args = ap.parse_args()

    prod_root = Path(args.prod_root).resolve()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(prod_root / "signal_engine"))
    from app.final14_config import FINAL14_CASES, snapshot as final14_snapshot
    from app.final14_research.two_trail_layer12 import WINDOWS, END

    datasets = {
        "BTC-USDT": args.btc,
        "ETH-USDT": args.eth,
    }
    built = {}
    for symbol, path in datasets.items():
        d, events = build_events(prod_root, path, symbol)
        built[symbol] = (d, events)

    models = {
        "cross_x100": 100.0,
        "isolated_x50": 50.0,
        "isolated_x100": 100.0,
    }

    summary_rows = []
    full_trade_rows = {}
    portfolio_rows = []

    for model, lev in models.items():
        model_full = {}
        for symbol, (d, events_by_combo) in built.items():
            for combo, cfg in FINAL14_CASES[symbol].items():
                cname = "TIER" if combo == 11 else f"C{combo}"
                for wn, start in WINDOWS.items():
                    metrics, rows = simulate_case(
                        d, events_by_combo[combo], cfg, start, END, model, lev, args.mmr
                    )
                    summary_rows.append({
                        "model": model,
                        "leverage": lev,
                        "symbol": symbol,
                        "combo": cname,
                        "window": wn,
                        "entry_variant": cfg["entry_variant"],
                        "layer2": cfg["layer2"],
                        "tp_pct": cfg["tp_pct"],
                        "sl_pct": cfg["sl_pct"],
                        "rr": cfg["rr"],
                        **metrics,
                    })
                    if wn == "FULL":
                        key = f"{symbol}:{cname}"
                        model_full[key] = rows
                        for r in rows:
                            full_trade_rows.setdefault(model, []).append({
                                "symbol": symbol,
                                "combo": cname,
                                **r,
                            })

        p = portfolio_from_trades(model_full, lev)
        p.update({
            "model": model,
            "leverage": lev,
            "margin_mode": "cross" if model == "cross_x100" else "isolated",
            "notional_per_trade": NOTIONAL,
            "mmr_proxy": args.mmr,
        })
        portfolio_rows.append(p)

    sdf = pd.DataFrame(summary_rows)
    sdf.to_csv(out / "FINAL14_PRODLOGIC_MARGIN_WINDOWS.csv", index=False)
    pd.DataFrame(portfolio_rows).to_csv(out / "FINAL14_PRODLOGIC_PORTFOLIO_FULL.csv", index=False)
    for model, rows in full_trade_rows.items():
        pd.DataFrame(rows).to_csv(out / f"TRADES_{model}_FULL.csv", index=False)

    # Direct model deltas by case/window.
    piv = sdf.pivot_table(
        index=["symbol", "combo", "window"],
        columns="model",
        values=["net_pnl", "liquidations", "trades"],
        aggfunc="first",
    )
    piv.to_csv(out / "FINAL14_PRODLOGIC_MODEL_DELTAS.csv")

    meta = {
        "production_ref": args.prod_ref,
        "production_strategy": final14_snapshot(),
        "dataset": {
            "BTC-USDT": {"path": args.btc, "sha256": sha256(args.btc)},
            "ETH-USDT": {"path": args.eth, "sha256": sha256(args.eth)},
        },
        "entry_source": "direct import from production signal_engine/app/final14_config.py and final14_research modules",
        "semantics": {
            "notional_per_trade": NOTIONAL,
            "fee_per_side": FEE,
            "initial_equity": INITIAL_EQUITY,
            "one_position_per_symbol_combo": True,
            "entry": "exact production-approved FINAL14 event set",
            "exit": "locked hard TP/SL; no exit on entry candle; adverse first on same-bar ambiguity",
            "margin_models": {
                "cross_x100": "account-level cross liquidation proxy, 100x",
                "isolated_x50": "isolated liquidation proxy, 50x",
                "isolated_x100": "isolated liquidation proxy, 100x reference",
            },
            "mmr_proxy": args.mmr,
            "slippage": "not included",
            "funding": "not included",
        },
    }
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("\nPRODUCTION STRATEGY\n" + json.dumps(final14_snapshot(), indent=2), flush=True)
    print("\nPORTFOLIO FULL\n" + pd.DataFrame(portfolio_rows).to_string(index=False), flush=True)

    # Focus output: compare 3Y and FULL by case.
    focus = sdf[sdf.window.isin(["3Y", "FULL"])][
        ["model", "symbol", "combo", "window", "trades", "net_pnl", "max_drawdown_realized", "liquidations"]
    ]
    print("\nCASE RESULTS 3Y/FULL\n" + focus.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
