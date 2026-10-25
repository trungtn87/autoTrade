from __future__ import annotations
"""20FINAL historical 50/50 fixed-TP + 0.5%-activation trailing backtest.

Uses the EXACT strategy signal/config imports from the checked-out V2 branch.
OHLC 15-minute bar model (conservative): entry at signal close, no exit on entry
bar, pre-existing stops take precedence on ambiguous bars, new trailing updates
effective from the NEXT candle; no slippage/funding/liquidation simulation.
"""
import argparse
import bisect
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from app.strategy.final14_config import FINAL14_CASES, NEW6_CASES, get_case, case_name
from app.strategy.final14_exact_strategy import _research_frame, _new6_filtered_signals
from app.strategy.final14_research.layer3_long import precompute, entry_variants, NATIVE
from app.strategy.final14_research.smc_structure import smc_direction
from app.strategy.final14_research.smc_ob import ob_context, OBConfig
from app.strategy.final14_research.two_trail_layer12 import approved

NOTIONAL = 100.0
FEE = 0.0005
INITIAL_EQUITY = 1000.0
TRAIL_ACTIVATION = 0.005
TRAIL_CALLBACK = 0.005
WINDOWS = {"1M": {"months": 1}, "3M": {"months": 3}, "6M": {"months": 6},
           "1Y": {"years": 1}, "3Y": {"years": 3}, "5Y": {"years": 5}}

def combo_key(combo):
    return case_name(combo) if combo >= 101 else ("TIER" if combo == 11 else f"C{combo}")

def build_events(symbol, raw):
    d = _research_frame(raw)
    wanted = {combo_key(c): {cfg["entry_variant"]} for c, cfg in FINAL14_CASES[symbol].items()}
    print(f"Computing signals for {symbol}, {len(d)} 15m candles ...", flush=True)
    pc = precompute(d, wanted)
    variants = entry_variants(pc, wanted)
    d1 = pc["d1"]
    smc15 = smc_direction(d, 50, False)
    smc1 = smc_direction(d1, 50, False)
    obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
    ob15 = ob_context(d, obcfg)
    ob1 = ob_context(d1, obcfg)
    pos = {t: i for i, t in enumerate(d.index)}
    out = defaultdict(list)

    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = combo_key(combo)
        chosen = [v for v in variants[cname] if v[0] == cfg["entry_variant"]]
        assert len(chosen) == 1, (symbol, cname, cfg["entry_variant"], len(chosen))
        _, long_s, short_s = chosen[0]
        native = NATIVE[cname]
        for side, series in (("L", long_s), ("S", short_s)):
            for ot in series.index[series.fillna(False)]:
                et = ot + pd.Timedelta("45min") if native == "1h" else ot
                ei = pos.get(et)
                if ei is None:
                    continue
                direction = smc1 if native == "1h" else smc15
                blocked_frame = ob1 if native == "1h" else ob15
                sd = int(direction.loc[ot]) if ot in direction.index else 0
                col = "buy_blocked" if side == "L" else "sell_blocked"
                blocked = bool(blocked_frame.loc[ot, col]) if ot in blocked_frame.index else False
                if approved(side, sd, blocked, cfg["layer2"]):
                    out[combo].append((int(ei), side))

    for combo, cfg in NEW6_CASES[symbol].items():
        long_s, short_s = _new6_filtered_signals(d1, combo, cfg, smc1, ob1)
        for side, series in (("L", long_s), ("S", short_s)):
            for ot in series.index[series.fillna(False)]:
                et = ot + pd.Timedelta("45min")
                ei = pos.get(et)
                if ei is not None:
                    out[combo].append((int(ei), side))
    for combo in out:
        out[combo].sort(key=lambda x: (x[0], x[1]))
    print(f"Signal count {symbol}: " + json.dumps({combo_key(c): len(e) for c, e in sorted(out.items())}), flush=True)
    return d, out

def run_trade(high, low, close, times, entry_i, side, cfg, end_i):
    """Returns trade row; each leg is half of 100 USDT position notional."""
    entry = float(close[entry_i])
    direction = 1 if side == "L" else -1
    tp = entry * (1 + direction * float(cfg["tp_pct"]))
    sl = entry * (1 - direction * float(cfg["sl_pct"]))
    activation = entry * (1 + direction * TRAIL_ACTIVATION)
    half_qty = NOTIONAL / (2 * entry)
    tp_live = True
    trail_live = True
    trail_active = False
    extreme = None
    trail_stop = None
    gross = 0.0
    fees = NOTIONAL * FEE
    exits = []
    last_exit_i = entry_i

    def close_leg(i, px, reason, leg):
        nonlocal gross, fees, last_exit_i
        gross += direction * (px - entry) * half_qty
        fees += px * half_qty * FEE
        last_exit_i = i
        exits.append({"leg": leg, "reason": reason, "price": round(px, 8), "time": str(times[i])})

    # No exit on entry candle: signal is available only at that candle's close.
    for i in range(entry_i + 1, end_i):
        if direction == 1:
            hit_sl = float(low[i]) <= sl
            hit_tp = float(high[i]) >= tp
        else:
            hit_sl = float(high[i]) >= sl
            hit_tp = float(low[i]) <= tp

        # Worst case on ambiguous OHLC: stop before TP or trailing updates.
        if tp_live:
            if hit_sl:
                close_leg(i, sl, "SL", "hard_tp_half")
                tp_live = False
            elif hit_tp:
                close_leg(i, tp, "TP", "hard_tp_half")
                tp_live = False

        if trail_live:
            effective_stop = sl
            if trail_active and trail_stop is not None:
                effective_stop = max(sl, trail_stop) if direction == 1 else min(sl, trail_stop)
            hit_stop = (float(low[i]) <= effective_stop) if direction == 1 else (float(high[i]) >= effective_stop)
            if hit_stop:
                reason = "TRAIL" if trail_active and (
                    (direction == 1 and effective_stop > sl) or
                    (direction == -1 and effective_stop < sl)) else "SL"
                close_leg(i, effective_stop, reason, "trailing_half")
                trail_live = False

        if not tp_live and not trail_live:
            break

        # New activation/extreme becomes effective only from the next 15m bar.
        if trail_live:
            favorable = float(high[i]) if direction == 1 else float(low[i])
            passed = favorable >= activation if direction == 1 else favorable <= activation
            if not trail_active and passed:
                trail_active = True
                extreme = favorable
                trail_stop = extreme * (1 - direction * TRAIL_CALLBACK)
            elif trail_active:
                extreme = max(extreme, favorable) if direction == 1 else min(extreme, favorable)
                candidate = extreme * (1 - direction * TRAIL_CALLBACK)
                trail_stop = max(trail_stop, candidate) if direction == 1 else min(trail_stop, candidate)

    mtm = bool(tp_live or trail_live)
    if mtm:
        i = end_i - 1
        px = float(close[i])
        if tp_live:
            close_leg(i, px, "END_MTM", "hard_tp_half")
        if trail_live:
            close_leg(i, px, "END_MTM", "trailing_half")
    reasons = {x["reason"] for x in exits}
    return {
        "entry_time": str(times[entry_i]), "exit_time": str(times[last_exit_i]),
        "side": side, "entry_price": round(entry, 8),
        "tp_price": round(tp, 8), "sl_price": round(sl, 8),
        "activation_price": round(activation, 8),
        "gross_pnl_usdt": round(gross, 8), "fees_usdt": round(fees, 8),
        "net_pnl_usdt": round(gross - fees, 8),
        "closed": not mtm, "reason": "+".join(sorted(reasons)),
        "exit_index": int(last_exit_i), "entry_index": int(entry_i),
        "exits": exits,
    }

def simulate_case(d, events, cfg, start_i, end_i, symbol, combo, window):
    high = d.high.to_numpy(float)
    low = d.low.to_numpy(float)
    close = d.close.to_numpy(float)
    times = d.index
    selected = [(i, s) for i, s in events if start_i <= i < end_i - 1]
    entries = [i for i, _ in selected]
    p = 0
    out = []
    while p < len(selected):
        ei, side = selected[p]
        row = run_trade(high, low, close, times, ei, side, cfg, end_i)
        row.update({"symbol": symbol, "combo": combo_key(combo), "combo_id": combo, "window": window})
        out.append(row)
        # Next strategy entry is allowed at the close of the exit candle.
        p = bisect.bisect_left(entries, row["exit_index"], p + 1)
    return out

def summarize(rows, start, end):
    n = len(rows)
    wins = sum(x["net_pnl_usdt"] > 0 for x in rows)
    losses = sum(x["net_pnl_usdt"] < 0 for x in rows)
    pnl = sum(x["net_pnl_usdt"] for x in rows)
    fees = sum(x["fees_usdt"] for x in rows)
    gross_win = sum(x["net_pnl_usdt"] for x in rows if x["net_pnl_usdt"] > 0)
    gross_loss = -sum(x["net_pnl_usdt"] for x in rows if x["net_pnl_usdt"] < 0)
    equity, peak, dd = INITIAL_EQUITY, INITIAL_EQUITY, 0.0
    for x in sorted(rows, key=lambda t: (t["exit_time"], t["symbol"], t["combo_id"])):
        equity += x["net_pnl_usdt"]
        peak = max(peak, equity)
        dd = max(dd, peak-equity)
    return {
        "from_utc": str(start), "to_utc": str(end), "trades": n,
        "wins": wins, "losses": losses, "win_rate_pct": round(100*wins/n, 4) if n else None,
        "net_pnl_usdt": round(pnl, 6), "fees_usdt": round(fees, 6),
        "profit_factor": round(gross_win/gross_loss, 5) if gross_loss else None,
        "max_drawdown_realized_usdt": round(dd, 6),
        "end_marked_open_trades": sum(not x["closed"] for x in rows),
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc", required=True)
    parser.add_argument("--eth", required=True)
    parser.add_argument("--out", default="results/backtest_20final_6windows")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {}
    for symbol, filename in (("BTC-USDT", args.btc), ("ETH-USDT", args.eth)):
        d = pd.read_pickle(filename)
        assert len(d) > 100000, (symbol, len(d))
        assert len(d.index.unique()) == len(d), "duplicate candle index"
        data[symbol] = build_events(symbol, d)
    end = min(d.index[-1] + pd.Timedelta(minutes=15) for d, _ in data.values())
    results = {}
    case_rows = []
    trade_rows = []
    for window, offset in WINDOWS.items():
        start = end - pd.DateOffset(**offset)
        rows = []
        for symbol, (d, events) in data.items():
            left, right = int(d.index.searchsorted(start)), int(d.index.searchsorted(end))
            for combo in sorted(set(FINAL14_CASES[symbol]) | set(NEW6_CASES[symbol])):
                rr = simulate_case(d, events.get(combo, []), get_case(symbol, combo), left, right, symbol, combo, window)
                rows.extend(rr)
                case_rows.append({"window": window, "symbol": symbol, "combo": combo_key(combo),
                                  "combo_id": combo, **summarize(rr, start, end)})
        trade_rows.extend(rows)
        results[window] = summarize(rows, start, end)
        print("WINDOW_RESULT=" + json.dumps({"window": window, **results[window]}), flush=True)
    meta = {
        "ok": True, "strategy": "20FINAL_2026-09-25",
        "source_branch": os.environ.get("GITHUB_REF_NAME", "local"),
        "source_commit": os.environ.get("GITHUB_SHA", ""),
        "data_source": "BTCUSDT_hybrid_15m + ETHUSDT_hybrid_15m, Binance-through-Mar-2026/BingX-since-Apr-2026",
        "data_bars": {s: len(d) for s, (d, _) in data.items()},
        "data_last_close_utc": str(end), "windows": results,
        "notional_per_trade_usdt": NOTIONAL, "starting_equity_for_drawdown_usdt": INITIAL_EQUITY,
        "fee_per_side": FEE, "split_tp_fraction": 0.5,
        "trailing_activation_pct": TRAIL_ACTIVATION, "trailing_callback_rate": TRAIL_CALLBACK,
        "execution_model": "one position per symbol+combo; close-of-confirmed-candle entry; no entry-bar exits; pre-existing SL first on ambiguous 15m bars; trailing update next candle; end MTM",
        "limitations": ["OHLC cannot recover intrabar ordering", "not exchange tick-by-tick", "no slippage", "no funding", "no liquidation or leverage-specific margin", "BingX differs from Binance in early dataset"],
    }
    (out_dir/"summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    pd.DataFrame(case_rows).to_csv(out_dir/"combos_by_window.csv", index=False)
    pd.DataFrame(trade_rows).to_json(out_dir/"trades_by_window.jsonl", orient="records", lines=True, force_ascii=False)
    print("BACKTEST_COMPLETE=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")), flush=True)

if __name__ == "__main__":
    main()
