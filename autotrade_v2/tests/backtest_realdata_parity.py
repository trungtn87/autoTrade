from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pandas as pd

from app.strategy.final14_config import FINAL14_CASES, NEW6_CASES
from app.strategy.final14_exact_strategy import (
    _new6_filtered_signals,
    _new6_raw_signals,
)
from app.strategy.final14_research.layer3_long import (
    build_context as new_build_context,
    entry_variants as new_entry_variants,
    precompute as new_precompute,
)
from app.strategy.final14_research.smc_ob import (
    OBConfig as NewOBConfig,
    ob_context as new_ob_context,
)
from app.strategy.final14_research.smc_structure import (
    smc_direction as new_smc_direction,
)
from app.strategy.final14_research.two_trail_layer12 import approved as new_approved


NEW6_EXPECTED = {
    "BTC-USDT": {
        101: {
            "layer2":"SMC_STRICT_OB","tp_pct":0.018,"sl_pct":0.009,
            "raw_events":2421,
            "raw_sha256":"6b70de5e557677b08b4e1a22b74ea8a61e9af85ef0080f2fcf44d02a56ce02b1",
            "passed_events":1873,
            "passed_sha256":"2b4760176d0c460452a6d8b451a68a91daffd10fdbc61fdccc83f4e15f6a679a",
        },
        104: {
            "layer2":"OB","tp_pct":0.020,"sl_pct":0.006666666666666667,
            "raw_events":711,
            "raw_sha256":"325db355787cd66f26de151ae01a6c2d38edfe40a23b66e97da4830184dd72e5",
            "passed_events":631,
            "passed_sha256":"5cce10257a5547f0ded2e16783e3df4cb2bd94e3a512d0d919fa0d53f0654311",
        },
        106: {
            "layer2":"OB","tp_pct":0.011,"sl_pct":0.009,
            "raw_events":407,
            "raw_sha256":"f4e2d70d8a07863c92ad1966f303c196ecea5e134208460090d60d2a55826ff3",
            "passed_events":352,
            "passed_sha256":"e23edc85a914059d764c564bccdead21d76417c0347ae4c398143ac685ab7de8",
        },
    },
    "ETH-USDT": {
        102: {
            "layer2":"SMC_VETO_OB","tp_pct":0.018,"sl_pct":0.009,
            "raw_events":1142,
            "raw_sha256":"9e027b4065931f1796c314a95fb7d82a6f336bea19b7ce3284b9559b5d6e0d4c",
            "passed_events":938,
            "passed_sha256":"f02f8dc0011c7249a0f441b43eed96a66d5679c8d36be546473489aaf5a5e8af",
        },
        103: {
            "layer2":"OFF","tp_pct":0.020,"sl_pct":0.008,
            "raw_events":615,
            "raw_sha256":"2405e837f252b952b11a95da2ea773f0ec1840ec561c77cd952dfc378f39bec4",
            "passed_events":615,
            "passed_sha256":"2405e837f252b952b11a95da2ea773f0ec1840ec561c77cd952dfc378f39bec4",
        },
        105: {
            "layer2":"OB","tp_pct":0.020,"sl_pct":0.008,
            "raw_events":3072,
            "raw_sha256":"205da32260a4ead62404416f5090ce40fbb16feabd852ad70ddf9c4298dfd777",
            "passed_events":3048,
            "passed_sha256":"b775d9b27dd3eb359325666130e959b9748b0e0dbe6b751aaca7becb26f0de8b",
        },
    },
}


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


def digest_plain(rows) -> str:
    payload=json.dumps(
        rows,
        separators=(",",":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_old(backtest_dir: Path):
    sys.path.insert(0, str(backtest_dir))
    try:
        return importlib.import_module("layer3_long")
    finally:
        sys.path.pop(0)


def new6_parity(live_symbol:str,d:pd.DataFrame,d1:pd.DataFrame)->dict:
    smc1=new_smc_direction(d1,50,False)
    obcfg=NewOBConfig(pivot_len=5,search_bars=12,max_age=80,danger_atr=0.5)
    ob1=new_ob_context(d1,obcfg)
    out={}

    for combo,cfg in NEW6_CASES[live_symbol].items():
        expected=NEW6_EXPECTED[live_symbol][combo]
        assert cfg["layer2"]==expected["layer2"], (live_symbol,combo,"layer2")
        assert abs(float(cfg["tp_pct"])-expected["tp_pct"])<1e-12
        assert abs(float(cfg["sl_pct"])-expected["sl_pct"])<1e-12

        raw_l,raw_s=_new6_raw_signals(d1,combo)
        passed_l,passed_s=_new6_filtered_signals(
            d1,combo,cfg,smc1,ob1
        )

        raw=[]
        passed=[]
        for i,ot in enumerate(d1.index):
            is_long=bool(raw_l.iloc[i])
            is_short=bool(raw_s.iloc[i])
            if is_long==is_short:
                continue
            side="L" if is_long else "S"
            sd=int(smc1.loc[ot])
            blocked=bool(
                ob1.loc[ot,"buy_blocked" if is_long else "sell_blocked"]
            )
            row=[int(pd.Timestamp(ot).value),side,sd,blocked]
            raw.append(row)
            if bool(passed_l.iloc[i]) or bool(passed_s.iloc[i]):
                passed.append(row)

        got={
            "entry_variant":cfg["entry_variant"],
            "layer2":cfg["layer2"],
            "raw_events":len(raw),
            "passed_events":len(passed),
            "raw_sha256":digest_plain(raw),
            "passed_sha256":digest_plain(passed),
        }
        assert got["raw_events"]==expected["raw_events"], (
            live_symbol,combo,"raw_count",got["raw_events"],expected["raw_events"]
        )
        assert got["passed_events"]==expected["passed_events"], (
            live_symbol,combo,"passed_count",got["passed_events"],expected["passed_events"]
        )
        assert got["raw_sha256"]==expected["raw_sha256"], (
            live_symbol,combo,"raw_hash"
        )
        assert got["passed_sha256"]==expected["passed_sha256"], (
            live_symbol,combo,"passed_hash"
        )
        out[f"N{combo-100}"]=got
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    ap.add_argument("--backtest-dir", required=True)
    args = ap.parse_args()

    old = load_old(Path(args.backtest_dir))
    results = {}
    new6_results={}

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
        new6_results[live_symbol]=new6_parity(
            live_symbol,d,new_pc["d1"]
        )

    print(json.dumps({
        "ok": True,
        "source_commit": "11c215db8cc1be1c0872360291f308bba7582cf7",
        "source_run": 35623101331,
        "dataset": "RUN17 hybrid 15m 2021-01-01..2026-09-20",
        "final14_cases_checked": sum(len(x) for x in FINAL14_CASES.values()),
        "new6_cases_checked": sum(len(x) for x in NEW6_CASES.values()),
        "results": results,
        "new6_results":new6_results,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
