from __future__ import annotations

import argparse
import time

import pandas as pd

from app.final14_config import FINAL14_CASES
from app.final14_research.layer3_long import load, precompute, entry_variants


def cname(combo: int) -> str:
    return "TIER" if int(combo) == 11 else f"C{int(combo)}"


def run_symbol(symbol: str, path: str) -> dict:
    # Production parity window: same locked 3400-bar warmup used by the live engine.
    d = load(path).tail(3400).copy()
    pc = precompute(d)
    wanted = {
        cname(combo): {cfg["entry_variant"]}
        for combo, cfg in FINAL14_CASES[symbol].items()
    }

    t0 = time.perf_counter()
    full = entry_variants(pc)
    full_sec = time.perf_counter() - t0

    t1 = time.perf_counter()
    fast = entry_variants(pc, wanted)
    fast_sec = time.perf_counter() - t1

    assert set(fast) == set(wanted), (symbol, set(fast), set(wanted))

    for combo, names in wanted.items():
        assert len(names) == 1
        target = next(iter(names))
        old = [row for row in full[combo] if row[0] == target]
        new = fast[combo]
        assert len(old) == 1, (symbol, combo, target, len(old))
        assert len(new) == 1, (symbol, combo, target, len(new))
        assert old[0][0] == new[0][0] == target
        pd.testing.assert_series_equal(old[0][1], new[0][1], check_names=True)
        pd.testing.assert_series_equal(old[0][2], new[0][2], check_names=True)

    return {
        "symbol": symbol,
        "bars": len(d),
        "locked_variants": len(wanted),
        "full_sec": round(full_sec, 4),
        "fast_sec": round(fast_sec, 4),
        "speedup": round(full_sec / fast_sec, 2) if fast_sec else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    args = ap.parse_args()

    results = [
        run_symbol("BTC-USDT", args.btc),
        run_symbol("ETH-USDT", args.eth),
    ]
    for row in results:
        print("FINAL14_FAST_VARIANT_PARITY", row, flush=True)
    print("FINAL14 selected-variant parity PASS", flush=True)


if __name__ == "__main__":
    main()
