from __future__ import annotations

import argparse
import json

from app.final14_config import FINAL14_CASES
from app.final14_research.layer3_long import NATIVE, entry_variants, load, precompute
from app.final14_research.signals import resample_ohlcv
from app.final14_research.smc_ob import OBConfig, ob_context
from app.final14_research.smc_structure import smc_direction

CANDIDATE_BARS = 3400
REFERENCE_BARS = 12000
RECENT_15M_BARS = 96
RECENT_1H_BARS = 24
ANCHOR_STEP_BARS = 7 * 24 * 4
ANCHOR_COUNT = 12


def _combo_name(combo: int) -> str:
    return "TIER" if int(combo) == 11 else f"C{int(combo)}"


def _selected_variants(d, symbol: str):
    pc = precompute(d)
    wanted = {
        _combo_name(combo): {cfg["entry_variant"]}
        for combo, cfg in FINAL14_CASES[symbol].items()
    }
    allv = entry_variants(pc, wanted)
    selected = {}
    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = _combo_name(combo)
        rows = [row for row in allv[cname] if row[0] == cfg["entry_variant"]]
        assert len(rows) == 1, (symbol, cname, cfg["entry_variant"], len(rows))
        selected[cname] = rows[0]
    return pc, selected


def _bool_signature(series, n: int):
    tail = series.tail(n)
    return [
        (int(ts.value // 1_000_000), bool(value))
        for ts, value in tail.items()
    ]


def _int_signature(series, n: int):
    tail = series.tail(n)
    return [
        (int(ts.value // 1_000_000), int(value))
        for ts, value in tail.items()
    ]


def _ob_signature(frame, n: int):
    tail = frame.tail(n)
    return [
        (
            int(ts.value // 1_000_000),
            bool(row["buy_blocked"]),
            bool(row["sell_blocked"]),
        )
        for ts, row in tail.iterrows()
    ]


def decision_signature(d, symbol: str) -> dict:
    pc, selected = _selected_variants(d, symbol)
    d1 = pc["d1"]

    raw = {}
    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = _combo_name(combo)
        _, long_series, short_series = selected[cname]
        recent = RECENT_1H_BARS if NATIVE[cname] == "1h" else RECENT_15M_BARS
        raw[cname] = {
            "long": _bool_signature(long_series, recent),
            "short": _bool_signature(short_series, recent),
        }

    obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
    return {
        "d4_count": int(len(pc["d4"])),
        "d6_count": int(len(pc["d6"])),
        "raw": raw,
        "smc15": _int_signature(smc_direction(d, 50, False), RECENT_15M_BARS),
        "smc1": _int_signature(smc_direction(d1, 50, False), RECENT_1H_BARS),
        "ob15": _ob_signature(ob_context(d, obcfg), RECENT_15M_BARS),
        "ob1": _ob_signature(ob_context(d1, obcfg), RECENT_1H_BARS),
    }


def _diff_keys(a: dict, b: dict) -> list[str]:
    diffs = []
    for key in ("raw", "smc15", "smc1", "ob15", "ob1"):
        if a[key] != b[key]:
            diffs.append(key)
    return diffs


def validate_symbol(symbol: str, path: str) -> dict:
    d = load(path)
    rows = []
    failures = []

    for anchor_no in range(ANCHOR_COUNT):
        stop = len(d) - anchor_no * ANCHOR_STEP_BARS
        if stop < REFERENCE_BARS:
            break

        ref = d.iloc[stop - REFERENCE_BARS:stop].copy()
        candidate = d.iloc[stop - CANDIDATE_BARS:stop].copy()
        ref_sig = decision_signature(ref, symbol)
        candidate_sig = decision_signature(candidate, symbol)
        diffs = _diff_keys(ref_sig, candidate_sig)

        row = {
            "anchor": anchor_no,
            "end": str(d.index[stop - 1]),
            "candidate_bars": len(candidate),
            "reference_bars": len(ref),
            "candidate_d4": candidate_sig["d4_count"],
            "candidate_d6": candidate_sig["d6_count"],
            "diff_keys": diffs,
            "pass": not diffs,
        }
        rows.append(row)
        if diffs:
            failures.append(row)

    return {
        "symbol": symbol,
        "anchors_tested": len(rows),
        "pass": not failures,
        "failures": failures[:10],
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    args = ap.parse_args()

    result = [
        validate_symbol("BTC-USDT", args.btc),
        validate_symbol("ETH-USDT", args.eth),
    ]
    print(json.dumps(result, indent=2), flush=True)

    failures = [row for row in result if not row["pass"]]
    if failures:
        raise SystemExit(
            "3400-bar decision parity FAIL vs 12000-bar production reference: "
            + repr([(row["symbol"], len(row["failures"])) for row in failures])
        )

    print(
        "FINAL14 3400-bar decision parity PASS vs 12000-bar reference "
        f"across {ANCHOR_COUNT} weekly anchors",
        flush=True,
    )


if __name__ == "__main__":
    main()
