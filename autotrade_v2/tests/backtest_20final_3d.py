from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
import pandas as pd

from app.data.historical import HistoricalKlineClient
from app.strategy.final14_config import FINAL14_CASES, NEW6_CASES, get_case, case_name
from app.strategy.final14_exact_strategy import _research_frame, _new6_filtered_signals
from app.strategy.final14_research.layer3_long import precompute, entry_variants, NATIVE
from app.strategy.final14_research.smc_structure import smc_direction
from app.strategy.final14_research.smc_ob import ob_context, OBConfig
from app.strategy.final14_research.two_trail_layer12 import approved

NOTIONAL = 100.0
FEE = 0.0005
INITIAL_EQUITY = 1000.0
BARS = 3400
WINDOW = pd.Timedelta(days=3)


def combo_key(combo: int) -> str:
    combo = int(combo)
    if combo >= 101:
        return case_name(combo)
    return "TIER" if combo == 11 else f"C{combo}"


def build_events(symbol: str, raw15: pd.DataFrame):
    d = _research_frame(raw15)
    configs = FINAL14_CASES[symbol]
    wanted = {
        combo_key(combo): {cfg["entry_variant"]}
        for combo, cfg in configs.items()
    }

    pc = precompute(d, wanted)
    allv = entry_variants(pc, wanted)
    selected = {}
    for combo, cfg in configs.items():
        cname = combo_key(combo)
        matches = [x for x in allv[cname] if x[0] == cfg["entry_variant"]]
        if len(matches) != 1:
            raise RuntimeError((symbol, cname, cfg["entry_variant"], len(matches)))
        selected[cname] = matches[0]

    d1 = pc["d1"]
    smc15 = smc_direction(d, 50, False)
    smc1 = smc_direction(d1, 50, False)
    obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
    ob15 = ob_context(d, obcfg)
    ob1 = ob_context(d1, obcfg)

    pos = {t: i for i, t in enumerate(d.index)}
    events = defaultdict(list)

    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = combo_key(combo)
        _, L, S = selected[cname]
        native = NATIVE[cname]
        for side, ser in (("L", L), ("S", S)):
            for ot in ser.index[ser.fillna(False)]:
                et = ot + pd.Timedelta("45min") if native == "1h" else ot
                if et not in pos:
                    continue
                if native == "1h":
                    sd = int(smc1.loc[ot]) if ot in smc1.index else 0
                    blocked = bool(ob1.loc[ot, "buy_blocked" if side == "L" else "sell_blocked"]) if ot in ob1.index else False
                else:
                    sd = int(smc15.loc[ot]) if ot in smc15.index else 0
                    blocked = bool(ob15.loc[ot, "buy_blocked" if side == "L" else "sell_blocked"]) if ot in ob15.index else False
                if approved(side, sd, blocked, cfg["layer2"]):
                    events[int(combo)].append((pos[et], side, ot, "FINAL14"))

    for combo, cfg in NEW6_CASES[symbol].items():
        long1, short1 = _new6_filtered_signals(d1, combo, cfg, smc1, ob1)
        for side, ser in (("L", long1), ("S", short1)):
            for ot in ser.index[ser.fillna(False)]:
                et = ot + pd.Timedelta("45min")
                if et in pos:
                    events[int(combo)].append((pos[et], side, ot, "NEW6"))

    for combo in events:
        events[combo].sort(key=lambda x: x[0])
    return d, events


def simulate_case(d: pd.DataFrame, events, cfg, start_i: int, end_i: int):
    H = d.high.to_numpy(float)
    L = d.low.to_numpy(float)
    C = d.close.to_numpy(float)
    idx = d.index

    ev = [e for e in events if start_i <= e[0] < end_i]
    p = 0
    rows = []

    while p < len(ev):
        ei, side, ot, strategy = ev[p]
        entry = float(C[ei])
        tp_pct = float(cfg["tp_pct"])
        sl_pct = float(cfg["sl_pct"])
        if side == "L":
            tp = entry * (1 + tp_pct)
            sl = entry * (1 - sl_pct)
        else:
            tp = entry * (1 - tp_pct)
            sl = entry * (1 + sl_pct)

        exit_i = None
        exit_px = None
        reason = None
        for i in range(ei + 1, end_i):
            hit_sl = L[i] <= sl if side == "L" else H[i] >= sl
            hit_tp = H[i] >= tp if side == "L" else L[i] <= tp
            if hit_sl or hit_tp:
                exit_i = i
                if hit_sl:
                    exit_px = sl
                    reason = "SL"
                else:
                    exit_px = tp
                    reason = "TP"
                break

        if exit_i is None:
            exit_i = end_i - 1
            exit_px = float(C[exit_i])
            reason = "END_MTM"

        qty = NOTIONAL / entry
        gross = (exit_px - entry) * qty * (1 if side == "L" else -1)
        fees = NOTIONAL * FEE + exit_px * qty * FEE
        pnl = gross - fees

        rows.append({
            "entry_time": str(idx[ei]),
            "exit_time": str(idx[exit_i]),
            "side": side,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "exit": exit_px,
            "reason": reason,
            "gross_pnl": gross,
            "fees": fees,
            "net_pnl": pnl,
            "bars_held": int(exit_i - ei),
            "strategy": strategy,
        })

        p += 1
        while p < len(ev) and ev[p][0] < exit_i:
            p += 1

    return rows


def main():
    client = HistoricalKlineClient()
    built = {}
    latest_times = []

    for symbol in ("BTC-USDT", "ETH-USDT"):
        raw = client.fetch_history(symbol, BARS)
        d, events = build_events(symbol, raw)
        built[symbol] = (d, events)
        latest_times.append(d.index[-1])

    end_ts = min(latest_times) + pd.Timedelta("15min")
    start_ts = end_ts - WINDOW

    all_rows = []
    case_summary = []

    for symbol, (d, events) in built.items():
        start_i = int(d.index.searchsorted(start_ts))
        end_i = int(d.index.searchsorted(end_ts))
        all_case_ids = sorted(set(FINAL14_CASES[symbol]) | set(NEW6_CASES[symbol]))
        for combo in all_case_ids:
            cfg = get_case(symbol, combo)
            rows = simulate_case(d, events.get(combo, []), cfg, start_i, end_i)
            name = combo_key(combo)
            for r in rows:
                r.update({"symbol": symbol, "combo": name, "combo_id": int(combo)})
                all_rows.append(r)
            wins = sum(r["net_pnl"] > 0 for r in rows)
            losses = sum(r["net_pnl"] < 0 for r in rows)
            case_summary.append({
                "symbol": symbol,
                "combo": name,
                "combo_id": int(combo),
                "trades": len(rows),
                "wins": wins,
                "losses": losses,
                "net_pnl": sum(r["net_pnl"] for r in rows),
            })

    all_rows.sort(key=lambda r: (r["entry_time"], r["symbol"], r["combo_id"]))
    trades = len(all_rows)
    wins = sum(r["net_pnl"] > 0 for r in all_rows)
    losses = sum(r["net_pnl"] < 0 for r in all_rows)
    net = sum(r["net_pnl"] for r in all_rows)
    total_fees = sum(r["fees"] for r in all_rows)

    exits = sorted((pd.Timestamp(r["exit_time"]), r["net_pnl"]) for r in all_rows)
    equity = INITIAL_EQUITY
    peak = equity
    max_dd = 0.0
    for _, pnl in exits:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    summary = {
        "ok": True,
        "strategy": "20FINAL_2026-09-25",
        "source_branch": "autotrade-v2-clean",
        "source_commit": "33a0f92fbf3b1295d3e0a8f2fe490e35eef92ece",
        "window_start_utc": str(start_ts),
        "window_end_utc": str(end_ts),
        "bars_per_symbol": BARS,
        "notional_per_trade_usdt": NOTIONAL,
        "fee_per_side": FEE,
        "initial_equity": INITIAL_EQUITY,
        "semantics": "one position per symbol+combo; no exit on entry candle; SL first on same-bar ambiguity; hard TP/SL",
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": wins / trades * 100 if trades else None,
        "net_pnl_usdt": net,
        "fees_usdt": total_fees,
        "ending_equity": INITIAL_EQUITY + net,
        "max_drawdown_realized_usdt": max_dd,
        "active_combos": [x for x in case_summary if x["trades"] > 0],
        "zero_trade_combos": [x for x in case_summary if x["trades"] == 0],
        "trades_detail": all_rows,
    }
    print("RESULT_JSON=" + json.dumps(summary, separators=(",", ":"), ensure_ascii=True))


if __name__ == "__main__":
    main()
