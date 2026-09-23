"""Offline regression against the pinned pre-refactor production source.

No exchange, database, or Discord calls. Compare every selected raw event,
then replay real event timestamps through both complete scan implementations.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.util
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np
import pandas as pd

from app import final14_exact_strategy as live
from app.final14_config import FINAL14_CASES, case_name
from app.final14_research import layer3_long as research


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_frame():
    rng = np.random.default_rng(14)
    n = 12512
    close = 30000 * np.exp(np.cumsum(rng.normal(0, .005, n)))
    op = np.r_[close[0], close[:-1]]
    wick = rng.uniform(.0001, .003, n) * close
    index = pd.date_range('2026-04-01', periods=n, freq='15min', tz='UTC')
    return pd.DataFrame({
        'open': op, 'high': np.maximum(op, close) + wick,
        'low': np.minimum(op, close) - wick, 'close': close,
        'volume': rng.lognormal(7, 1.1, n),
        'open_time': index.asi8 // 1_000_000,
        'close_time': index.asi8 // 1_000_000 + 899999,
    }, index=index)


def run(baseline_root, datasets):
    base = Path(baseline_root) / 'signal_engine/app'
    old_research = load_module('app.final14_research._before', base / 'final14_research/layer3_long.py')
    old = load_module('app._before', base / 'final14_exact_strategy.py')
    old.entry_variants = old_research.entry_variants
    old.precompute = old_research.precompute
    total_events = total_replays = 0
    old_seconds = new_seconds = 0.

    for symbol, data in datasets.items():
        data = live._research_frame(data)
        pc = old_research.precompute(data)
        before = old_research.entry_variants(pc)
        requested = {case_name(c): cfg['entry_variant'] for c, cfg in FINAL14_CASES[symbol].items()}
        after = research.entry_variants(pc, selected=requested)
        assert set(after) == set(requested)
        timestamps = set(data.index[-4:])
        for combo, name in requested.items():
            ref = [v for v in before[combo] if v[0] == name]
            assert len(ref) == len(after[combo]) == 1
            for original, optimized in zip(ref[0][1:], after[combo][0][1:]):
                pd.testing.assert_series_equal(original, optimized, check_exact=True)
                total_events += int(original.sum())
                # One recent event per direction/case, plus all quarter-hour phases.
                shift = pd.Timedelta('45min') if research.NATIVE[combo] == '1h' else pd.Timedelta(0)
                events = original.index[original] + shift
                events = events[(events >= data.index[11999]) & (events <= data.index[-1])]
                if len(events):
                    timestamps.add(events[-1])
        print(f'{symbol}: all {len(requested)} raw event series identical', flush=True)

        for timestamp in sorted(timestamps):
            frame = data.loc[:timestamp]
            t = time.perf_counter()
            expected = old.scan_latest(symbol, frame)
            old_seconds += time.perf_counter() - t
            t = time.perf_counter()
            actual = live.scan_latest(symbol, frame)
            new_seconds += time.perf_counter() - t
            assert [asdict(s) for s in actual] == [asdict(s) for s in expected], (symbol, timestamp)
            assert [s.event_id for s in actual] == [s.event_id for s in expected]
            total_replays += 1

        # Explicitly disabled 1H, warmup threshold, and lazy context behavior.
        frame = data.iloc[:12000]
        assert live.scan_latest(symbol, frame, include_1h=False) == old.scan_latest(symbol, frame, include_1h=False)
        assert live.scan_latest(symbol, frame.iloc[:-1]) == []
        with patch.object(live, '_selected_variants', return_value={}), \
             patch.object(live, 'smc_direction', side_effect=AssertionError('unused SMC')), \
             patch.object(live, 'ob_context', side_effect=AssertionError('unused OB')):
            assert live.scan_latest(symbol, frame) == []

    print(f'PASS: 14 cases, {total_events} raw events, {total_replays} complete scan replays')
    print(f'Scan wall time: before={old_seconds:.3f}s after={new_seconds:.3f}s speedup={old_seconds/new_seconds:.2f}x')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-root', required=True)
    parser.add_argument('--btc')
    parser.add_argument('--eth')
    args = parser.parse_args()
    if bool(args.btc) != bool(args.eth):
        parser.error('--btc and --eth must be supplied together')
    datasets = ({'BTC-USDT': research.load(args.btc), 'ETH-USDT': research.load(args.eth)}
                if args.btc else {s: synthetic_frame() for s in FINAL14_CASES})
    run(args.baseline_root, datasets)
