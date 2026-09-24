from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pandas as pd

from app.strategy.final14_config import FINAL14_CASES
from app.strategy.final14_research.layer3_long import (
    build_context as new_build_context,
    entry_variants as new_entry_variants,
    precompute as new_precompute,
)
from app.strategy.final14_research.two_trail_layer12 import approved as new_approved


def combo_name(combo: int) -> str:
    return "TIER" if int(combo) == 11 else f"C{int(combo)}"


def normalize_events(events):
    out = []
    for pos, side, ot, smc_dir, blocked in events:
        ts = pd.Timestamp(ot)
        out.append([
            int(pos),
            str(side),
            int(ts.value),
            int(smc_dir),
            bool(blocked),
        ])
    return out


def digest(events) -> str:
    payload = json.dumps(
        normalize_events(events),
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_old(backtest_dir: Path):
    sys.path.insert(0, str(backtest_dir))
    try:
        return importlib.import_module("layer3_long")
    finally:
        sys.path.pop(0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    ap.add_argument("--backtest-dir", required=True)
    args = ap.parse_args()

    old = load_old(Path(args.backtest_dir))
    results = {}

    for live_symbol, backtest_symbol, dataset in (
        ("BTC-USDT", "BTCUSDT", args.btc),
        ("ETH-USDT", "ETHUSDT", args.eth),
    ):
        d = old.load(dataset)

        selected = {
            combo_name(combo): {cfg["entry_variant"]}
            for combo, cfg in FINAL14_CASES[live_symbol].items()
        }
        lock = {
            combo_name(combo): {"layer2_variant": cfg["layer2"]}
            for combo, cfg in FINAL14_CASES[live_symbol].items()
        }

        old_pc = old.precompute(d)
        old_all = old.entry_variants(old_pc)
        old_selected = {}
        for cname, names in selected.items():
            matches = [x for x in old_all[cname] if x[0] in names]
            assert len(matches) == 1, (backtest_symbol, cname, names, len(matches))
            old_selected[cname] = matches

        new_pc = new_precompute(d, selected)
        new_all = new_entry_variants(new_pc, selected)
        new_selected = {}
        for cname, names in selected.items():
            matches = [x for x in new_all[cname] if x[0] in names]
            assert len(matches) == 1, (live_symbol, cname, names, len(matches))
            new_selected[cname] = matches

        old_ctx = old.build_context(d, lock, old_selected)
        new_ctx = new_build_context(d, lock, new_selected)

        symbol_results = {}
        for combo, cfg in FINAL14_CASES[live_symbol].items():
            cname = combo_name(combo)
            variant = cfg["entry_variant"]
            layer2 = cfg["layer2"]

            old_events = old_ctx[cname][variant]
            new_events = new_ctx[cname][variant]
            assert normalize_events(new_events) == normalize_events(old_events), (
                live_symbol,
                cname,
                "raw_event_mismatch",
                len(old_events),
                len(new_events),
            )

            old_passed = [
                e for e in old_events
                if old.approved(e[1], e[3], e[4], layer2)
            ]
            new_passed = [
                e for e in new_events
                if new_approved(e[1], e[3], e[4], layer2)
            ]
            assert normalize_events(new_passed) == normalize_events(old_passed), (
                live_symbol,
                cname,
                "layer2_pass_mismatch",
                len(old_passed),
                len(new_passed),
            )

            symbol_results[cname] = {
                "entry_variant": variant,
                "layer2": layer2,
                "raw_events": len(old_events),
                "passed_events": len(old_passed),
                "raw_sha256": digest(old_events),
                "passed_sha256": digest(old_passed),
            }

        results[live_symbol] = symbol_results

    print(json.dumps({
        "ok": True,
        "source_commit": "11c215db8cc1be1c0872360291f308bba7582cf7",
        "source_run": 35623101331,
        "dataset": "RUN17 hybrid 15m 2021-01-01..2026-09-20",
        "cases_checked": sum(len(x) for x in FINAL14_CASES.values()),
        "results": results,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
