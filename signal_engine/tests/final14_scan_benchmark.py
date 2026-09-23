from __future__ import annotations

import argparse
import time

from app.final14_exact_strategy import scan_latest
from app.final14_research.layer3_long import load


def bench(symbol: str, path: str) -> dict:
    d = load(path).tail(12000).copy()
    started = time.perf_counter()
    signals = scan_latest(symbol=symbol, m15=d)
    elapsed = time.perf_counter() - started
    return {
        "symbol": symbol,
        "bars": len(d),
        "signals": len(signals),
        "elapsed_sec": round(elapsed, 4),
        "signal_ids": [s.event_id for s in signals],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    args = ap.parse_args()

    for row in (
        bench("BTC-USDT", args.btc),
        bench("ETH-USDT", args.eth),
    ):
        print("FINAL14_SCAN_BENCHMARK", row, flush=True)

    print("FINAL14 live scan benchmark PASS", flush=True)


if __name__ == "__main__":
    main()
