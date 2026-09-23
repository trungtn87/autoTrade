from __future__ import annotations

import argparse
import json
import math
import sys

import numpy as np
import pandas as pd

from app.final14_exact_strategy import scan_latest
from app.timeframes import aggregate_15m
from final14_locked_manifest import LOCKED_FINAL14_CASES

WINDOW_BARS = 3400
POSITIVE_ANCHORS_PER_COMBO = 3
NEGATIVE_1H_ANCHORS = 4
NEGATIVE_15M_ANCHORS = 4


def _combo_name(combo: int) -> str:
    return "TIER" if int(combo) == 11 else f"C{int(combo)}"


def _as_research_frame(raw: pd.DataFrame) -> pd.DataFrame:
    d = raw.copy(deep=True)
    if "open_time" not in d.columns:
        if isinstance(d.index, pd.DatetimeIndex):
            d["open_time"] = (d.index.view("int64") // 1_000_000).astype("int64")
        else:
            raise RuntimeError("dataset missing open_time")
    if "close_time" not in d.columns:
        d["close_time"] = d["open_time"].astype("int64") + 15 * 60_000 - 1
    d = d.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
    d.index = pd.to_datetime(d["open_time"], unit="ms", utc=True)
    return d


def _load_raw(path: str) -> pd.DataFrame:
    return _as_research_frame(pd.read_pickle(path))


def _import_reference(research_dir: str):
    if research_dir not in sys.path:
        sys.path.insert(0, research_dir)
    import layer3_long as ref_l3
    from two_trail_layer12 import approved as ref_approved
    return ref_l3, ref_approved


def _reference_context(d: pd.DataFrame, symbol: str, ref_l3):
    pc = ref_l3.precompute(d)
    variants = ref_l3.entry_variants(pc)
    selected = {}
    lock = {}
    for combo, cfg in LOCKED_FINAL14_CASES[symbol].items():
        cname = _combo_name(combo)
        matches = [row for row in variants[cname] if row[0] == cfg["entry_variant"]]
        if len(matches) != 1:
            raise RuntimeError(
                f"reference variant mismatch {symbol} {cname} {cfg['entry_variant']}: {len(matches)}"
            )
        selected[cname] = matches
        lock[cname] = {"layer2_variant": cfg["layer2"]}
    return ref_l3.build_context(d, lock, selected)


def _approved_positions(d: pd.DataFrame, symbol: str, ref_l3, ref_approved):
    ctx = _reference_context(d, symbol, ref_l3)
    by_combo: dict[int, list[int]] = {}
    all_positions: set[int] = set()
    for combo, cfg in LOCKED_FINAL14_CASES[symbol].items():
        cname = _combo_name(combo)
        rows = ctx[cname][cfg["entry_variant"]]
        approved_positions = [
            int(pos)
            for pos, side, ot, sd, blocked in rows
            if ref_approved(side, int(sd), bool(blocked), cfg["layer2"])
            and int(pos) >= WINDOW_BARS - 1
        ]
        by_combo[int(combo)] = approved_positions
        all_positions.update(approved_positions)
    return by_combo, all_positions


def _select_anchors(d: pd.DataFrame, by_combo: dict[int, list[int]], all_events: set[int]):
    positive: set[int] = set()
    source_by_anchor: dict[int, list[int]] = {}
    for combo, positions in sorted(by_combo.items()):
        picks = positions[-POSITIVE_ANCHORS_PER_COMBO:]
        for pos in picks:
            positive.add(pos)
            source_by_anchor.setdefault(pos, []).append(combo)

    start = max(WINDOW_BARS - 1, len(d) - 12_000)
    candidates = np.linspace(start, len(d) - 1, num=min(240, max(1, len(d) - start)), dtype=int)
    negative_1h: list[int] = []
    negative_15m: list[int] = []
    for pos in candidates[::-1]:
        pos = int(pos)
        if pos in all_events or pos in positive:
            continue
        minute = int(d.index[pos].minute)
        if minute == 45 and len(negative_1h) < NEGATIVE_1H_ANCHORS:
            negative_1h.append(pos)
        elif minute != 45 and len(negative_15m) < NEGATIVE_15M_ANCHORS:
            negative_15m.append(pos)
        if len(negative_1h) >= NEGATIVE_1H_ANCHORS and len(negative_15m) >= NEGATIVE_15M_ANCHORS:
            break

    anchors = sorted(positive | set(negative_1h) | set(negative_15m))
    return anchors, source_by_anchor, negative_1h, negative_15m


def _reference_latest(d: pd.DataFrame, symbol: str, ref_l3, ref_approved):
    ctx = _reference_context(d, symbol, ref_l3)
    last_pos = len(d) - 1
    entry = float(d.iloc[-1]["close"])
    close_time = int(d.iloc[-1]["close_time"])
    out = []
    for combo, cfg in LOCKED_FINAL14_CASES[symbol].items():
        cname = _combo_name(combo)
        native = str(ref_l3.NATIVE[cname])
        for pos, side, ot, sd, blocked in ctx[cname][cfg["entry_variant"]]:
            if int(pos) != last_pos:
                continue
            if not ref_approved(side, int(sd), bool(blocked), cfg["layer2"]):
                continue
            direction = 1 if side == "L" else -1
            if direction == 1:
                tp = entry * (1.0 + float(cfg["tp_pct"]))
                sl = entry * (1.0 - float(cfg["sl_pct"]))
                side_text = "BUY"
            else:
                tp = entry * (1.0 - float(cfg["tp_pct"]))
                sl = entry * (1.0 + float(cfg["sl_pct"]))
                side_text = "SELL"
            out.append({
                "combo": int(combo),
                "side": side_text,
                "timeframe": "1h" if native == "1h" else "15m",
                "close_time": close_time,
                "entry": entry,
                "tp": tp,
                "sl": sl,
                "smc_dir": int(sd),
            })
    return sorted(out, key=lambda x: (x["combo"], x["side"]))


def _production_latest(raw_window: pd.DataFrame, symbol: str):
    m15 = raw_window.reset_index(drop=True).copy()
    h1 = aggregate_15m(m15, 60)
    h4 = aggregate_15m(m15, 240)
    h6 = aggregate_15m(m15, 360)
    signals = scan_latest(
        symbol=symbol,
        m15=m15,
        h1=h1,
        h4=h4,
        h6=h6,
        include_1h=True,
    )
    return sorted([
        {
            "combo": int(sig.combo),
            "side": sig.side,
            "timeframe": sig.timeframe,
            "close_time": int(sig.close_time),
            "entry": float(sig.entry),
            "tp": float(sig.tp),
            "sl": float(sig.sl),
            "smc_dir": int(sig.smc_dir),
        }
        for sig in signals
    ], key=lambda x: (x["combo"], x["side"]))


def _equal_signal(a: dict, b: dict) -> bool:
    exact = ("combo", "side", "timeframe", "close_time", "smc_dir")
    if any(a[k] != b[k] for k in exact):
        return False
    for key in ("entry", "tp", "sl"):
        if not math.isclose(float(a[key]), float(b[key]), rel_tol=1e-12, abs_tol=1e-9):
            return False
    return True


def _signals_equal(expected: list[dict], actual: list[dict]) -> bool:
    return len(expected) == len(actual) and all(
        _equal_signal(e, a) for e, a in zip(expected, actual)
    )


def validate_symbol(symbol: str, path: str, research_dir: str) -> dict:
    ref_l3, ref_approved = _import_reference(research_dir)
    full = _load_raw(path)
    by_combo, all_events = _approved_positions(full, symbol, ref_l3, ref_approved)
    anchors, source_by_anchor, neg1h, neg15 = _select_anchors(full, by_combo, all_events)

    failures = []
    tested_expected_combos: set[int] = set()
    rows = []

    for pos in anchors:
        start = pos - WINDOW_BARS + 1
        if start < 0:
            continue
        window = full.iloc[start:pos + 1].copy()
        expected = _reference_latest(window, symbol, ref_l3, ref_approved)
        actual = _production_latest(window, symbol)
        tested_expected_combos.update(int(x["combo"]) for x in expected)

        ok = _signals_equal(expected, actual)
        row = {
            "position": int(pos),
            "open_time": int(full.iloc[pos]["open_time"]),
            "timestamp": str(full.index[pos]),
            "source_combos": sorted(source_by_anchor.get(pos, [])),
            "expected_count": len(expected),
            "actual_count": len(actual),
            "pass": ok,
        }
        rows.append(row)
        if not ok:
            failures.append({
                **row,
                "expected": expected,
                "actual": actual,
            })

    locked_combos = set(int(x) for x in LOCKED_FINAL14_CASES[symbol])
    uncovered = sorted(locked_combos - tested_expected_combos)

    if len(neg1h) < NEGATIVE_1H_ANCHORS or len(neg15) < NEGATIVE_15M_ANCHORS:
        failures.append({
            "coverage_error": "insufficient negative anchors",
            "negative_1h": len(neg1h),
            "negative_15m": len(neg15),
        })

    if uncovered:
        failures.append({
            "coverage_error": "no 3400-window approved reference event tested for locked combo",
            "uncovered_combos": uncovered,
        })

    return {
        "symbol": symbol,
        "anchors_tested": len(rows),
        "positive_anchor_count": len(source_by_anchor),
        "negative_1h_count": len(neg1h),
        "negative_15m_count": len(neg15),
        "covered_combos": sorted(tested_expected_combos),
        "pass": not failures,
        "failures": failures[:20],
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    ap.add_argument("--research-dir", required=True)
    args = ap.parse_args()

    result = [
        validate_symbol("BTC-USDT", args.btc, args.research_dir),
        validate_symbol("ETH-USDT", args.eth, args.research_dir),
    ]
    print(json.dumps(result, indent=2), flush=True)

    failed = [row for row in result if not row["pass"]]
    if failed:
        raise SystemExit(
            "FINAL14 LIVE scan parity FAIL: "
            + repr([(row["symbol"], len(row["failures"])) for row in failed])
        )

    print(
        "FINAL14 LIVE scan parity PASS: scan_latest() matches locked research "
        "backtest on positive and negative 3400-bar anchors",
        flush=True,
    )


if __name__ == "__main__":
    main()
